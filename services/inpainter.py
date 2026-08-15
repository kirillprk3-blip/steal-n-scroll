"""Inpainting: удаление текста со слайдов через LaMa ONNX.

Пайплайн:
1. RapidOCR — детекция текстовых блоков (bounding boxes)
2. Бинарная маска — белые прямоугольники на черном фоне (padding 5px)
3. LaMa ONNX (big-lama, 208MB) — инпейнт маскированных областей

Модель: Carve/LaMa-ONNX (lama_fp32.onnx), фиксированный вход 512×512.
Изображения любого размера паддятся до квадрата и ресайзятся перед инференсом.

Тяжёлые операции (OCR, инференс) запускаются через asyncio.to_thread.
"""

import asyncio
import logging
import os
import urllib.request
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("hoopbot.inpainter")

_LAMA_MODEL_URL = (
    "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
)
_MODEL_DIR = os.path.join("data", "models")
_LAMA_MODEL_PATH = os.path.join(_MODEL_DIR, "big-lama.onnx")

# Глобальные синглтоны (инициализируются лениво)
_ocr_instance = None
_lama_session = None


# ── lazy init ──────────────────────────────────────────────────────────────

def _init_ocr():
    """Инициализирует RapidOCR (однократно)."""
    global _ocr_instance
    if _ocr_instance is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_instance = RapidOCR()
        log.info("RapidOCR initialized")
    return _ocr_instance


def _ensure_model():
    """Скачивает LaMa ONNX модель, если ещё нет."""
    if os.path.exists(_LAMA_MODEL_PATH):
        log.info("LaMa model found at %s (%d bytes)",
                  _LAMA_MODEL_PATH, os.path.getsize(_LAMA_MODEL_PATH))
        return
    os.makedirs(_MODEL_DIR, exist_ok=True)
    log.info("Downloading LaMa ONNX model (208 MB) from %s ...", _LAMA_MODEL_URL)
    urllib.request.urlretrieve(_LAMA_MODEL_URL, _LAMA_MODEL_PATH)
    log.info("LaMa model downloaded: %s (%d bytes)",
              _LAMA_MODEL_PATH, os.path.getsize(_LAMA_MODEL_PATH))


def _init_lama():
    """Инициализирует ONNX Runtime сессию (однократно)."""
    global _lama_session
    if _lama_session is None:
        _ensure_model()
        log.info("Loading LaMa ONNX model from %s ...", _LAMA_MODEL_PATH)
        import onnxruntime
        _lama_session = onnxruntime.InferenceSession(
            _LAMA_MODEL_PATH,
            providers=["CPUExecutionProvider"],
        )
        log.info("LaMa ONNX session created (inputs=%s, outputs=%s)",
                  [i.name for i in _lama_session.get_inputs()],
                  [o.name for o in _lama_session.get_outputs()])
    return _lama_session


# ── OCR: bounding boxes → mask ────────────────────────────────────────────

def _ocr_to_mask(img: np.ndarray) -> Optional[np.ndarray]:
    """Распознаёт текст и возвращает бинарную маску (0/255).

    Returns:
        mask (H, W, uint8) или None если текст не найден.
    """
    ocr = _init_ocr()
    result, elapse = ocr(img)
    if not result:
        log.info("OCR: текстовые блоки не найдены, elapse=%.2f", elapse if elapse else 0)
        return None

    log.info("OCR: найдено %d текстовых блоков, elapse=%.2f", len(result), elapse if elapse else 0)

    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)

    for i, item in enumerate(result):
        # item = (box, text, score) или (box, text)
        box = item[0]
        detected_text = item[1] if len(item) > 1 and isinstance(item[1], str) else f"block_{i}"
        pts = np.array(box, dtype=np.int32).reshape(-1, 2)
        x_min = max(0, int(pts[:, 0].min()) - 5)
        y_min = max(0, int(pts[:, 1].min()) - 5)
        x_max = min(w, int(pts[:, 0].max()) + 5)
        y_max = min(h, int(pts[:, 1].max()) + 5)
        mask[y_min:y_max, x_min:x_max] = 255
        log.debug("OCR block %d: '%s' → box [%d,%d,%d,%d]", i, detected_text, x_min, y_min, x_max, y_max)

    return mask


# ── pad & resize to 512×512 ───────────────────────────────────────────────

def _prepare_for_lama(img: np.ndarray, mask: np.ndarray):
    """Паддит изображение и маску до квадрата, ресайзит до 512×512.

    Returns:
        (img_resized, mask_resized, orig_h, orig_w, pad_info)
    """
    h, w = img.shape[:2]
    side = max(h, w)

    # Паддинг до квадрата
    top = (side - h) // 2
    bottom = side - h - top
    left = (side - w) // 2
    right = side - w - left

    img_padded = cv2.copyMakeBorder(img, top, bottom, left, right,
                                     cv2.BORDER_REFLECT)
    mask_padded = cv2.copyMakeBorder(mask, top, bottom, left, right,
                                      cv2.BORDER_CONSTANT, value=0)

    # Ресайз до 512×512
    img_resized = cv2.resize(img_padded, (512, 512), interpolation=cv2.INTER_LINEAR)
    mask_resized = cv2.resize(mask_padded, (512, 512), interpolation=cv2.INTER_NEAREST)

    log.debug("Prepare: %dx%d → pad %d,%d,%d,%d → 512×512", w, h, top, bottom, left, right)

    return img_resized, mask_resized, h, w, (top, bottom, left, right, side)


def _postprocess(output: np.ndarray, h: int, w: int, pad_info: tuple) -> np.ndarray:
    """Обрабатывает выход модели: ресайз обратно + обрезка паддинга.

    Returns:
        Изображение исходного размера (h, w).
    """
    top, bottom, left, right, side = pad_info

    # output shape: [1, 3, 512, 512] → [512, 512, 3]
    out_img = output[0].transpose(1, 2, 0)
    out_img = np.clip(out_img, -1, 1)
    out_img = ((out_img * 0.5 + 0.5) * 255).astype(np.uint8)

    # Ресайз обратно к квадратному размеру
    if side != 512:
        out_img = cv2.resize(out_img, (side, side), interpolation=cv2.INTER_LINEAR)

    # Обрезка паддинга
    out_img = out_img[top:top + h, left:left + w]

    return out_img


# ── публичная синхронная функция ──────────────────────────────────────────

def clean_image_text(image_bytes: bytes) -> bytes:
    """Удаляет текст с изображения через LaMa инпейнтинг.

    Args:
        image_bytes: сырые байты изображения (JPEG/PNG/WEBP).

    Returns:
        Очищенное изображение в JPEG (байты).
        При любой ошибке возвращает исходные байты без изменений.
    """
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            log.warning("Inpainting: cv2.imdecode вернул None (%d байт)", len(image_bytes))
            return image_bytes

        h, w = img.shape[:2]
        log.info("Inpainting: старт %dx%d, %d байт", w, h, len(image_bytes))

        if h < 30 or w < 30:
            log.warning("Inpainting: изображение слишком мало (%dx%d), пропуск", w, h)
            return image_bytes

        # 1. OCR → маска
        mask = _ocr_to_mask(img)
        if mask is None:
            log.info("Inpainting: OCR не нашёл текста, возвращаю оригинал")
            return image_bytes
        nonzero = cv2.countNonZero(mask)
        log.info("Inpainting: OCR нашёл текст, маска %d ненулевых пикселей (%.1f%%)",
                  nonzero, nonzero / (h * w) * 100)

        # 2. Pad + resize до 512×512
        img_prep, mask_prep, orig_h, orig_w, pad_info = _prepare_for_lama(img, mask)
        log.info("Inpainting: подготовка завершена (%dx%d → 512×512)", orig_w, orig_h)

        # 3. Нормализация модели [-1, 1]
        img_norm = img_prep.astype(np.float32) / 127.5 - 1.0   # [0,255] → [-1,1]
        mask_norm = (mask_prep > 127).astype(np.float32)        # {0,255} → {0,1}

        # 4. Инференс
        session = _init_lama()
        log.info("Inpainting: запуск инференса LaMa...")
        output = session.run(
            ["output"],
            {
                "image": img_norm[np.newaxis, ...].transpose(0, 3, 1, 2),
                "mask": mask_norm[np.newaxis, np.newaxis, ...],
            },
        )[0]  # shape [1, 3, 512, 512]
        log.info("Inpainting: инференс завершён, output shape %s", str(output.shape))

        # 5. Постпроцессинг
        result_img = _postprocess(output, orig_h, orig_w, pad_info)

        # 6. Кодирование в JPEG
        success, encoded = cv2.imencode(".jpg", result_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if success:
            log.info("Inpainting: успешно, %d байт на выходе (было %d)",
                      len(encoded), len(image_bytes))
            return encoded.tobytes()
        log.warning("Inpainting: cv2.imencode не удался")
        return image_bytes

    except Exception:
        log.exception("Inpainting failed, returning original image")
        return image_bytes


async def clean_image_text_async(image_bytes: bytes) -> bytes:
    """Асинхронная обёртка: запускает clean_image_text в потоке.

    Используется из aiogram handlers для неблокирующего инпейнтинга.
    """
    log.info("Inpainting: clean_image_text_async called, %d bytes", len(image_bytes))
    result = await asyncio.to_thread(clean_image_text, image_bytes)
    log.info("Inpainting: clean_image_text_async done, returned %d bytes", len(result))
    return result