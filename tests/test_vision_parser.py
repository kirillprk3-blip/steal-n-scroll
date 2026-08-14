"""Tests for ai_vision._parse_response() — the JSON+fallback parser.

This is the most error-prone function in the project since it handles
malformed LLM output. Cover: valid JSON, fenced JSON, free-text fallback,
edge cases (empty, partial, garbage).
"""

from services.ai_vision import _parse_response


class TestParseResponseValidJson:
    def test_standard_json(self):
        """Парсинг чистого валидного JSON."""
        raw = '{"original": "Catch and shoot", "translation": "Ловлю и бросаю", "design": "Текст внизу"}'
        result = _parse_response(raw)
        assert result["original"] == "Catch and shoot"
        assert result["translation"] == "Ловлю и бросаю"
        assert result["design"] == "Текст внизу"

    def test_json_with_whitespace(self):
        """JSON с лишними пробелами/переносами."""
        raw = '  \n{"original": "test", "translation": "тест", "design": "ok"}\n  '
        result = _parse_response(raw)
        assert result["original"] == "test"

    def test_json_extra_fields_ignored(self):
        """Лишние поля в JSON не ломают парсинг."""
        raw = '{"original": "A", "translation": "Б", "design": "В", "extra": "Г"}'
        result = _parse_response(raw)
        assert result["original"] == "A"
        assert result["translation"] == "Б"
        assert result["design"] == "В"


class TestParseResponseFencedJson:
    def test_fenced_code_block(self):
        """JSON внутри ```json ... ```."""
        raw = '```json\n{"original": "A", "translation": "Б", "design": "В"}\n```'
        result = _parse_response(raw)
        assert result["original"] == "A"

    def test_fenced_no_lang(self):
        """JSON внутри ``` ... ``` (без указания языка)."""
        raw = '```\n{"original": "A", "translation": "Б", "design": "В"}\n```'
        result = _parse_response(raw)
        assert result["original"] == "A"

    def test_fenced_with_surrounding_text(self):
        """Текст до и после блока."""
        raw = 'Вот результат:\n```json\n{"original": "A", "translation": "Б", "design": "В"}\n```\nКонец.'
        result = _parse_response(raw)
        assert result["original"] == "A"


class TestParseResponseFallback:
    def test_free_text_fallback(self):
        """Когда JSON не парсится, но есть метка «Перевод»."""
        raw = "Оригинал: Catch and shoot\nПеревод: Ловлю и бросаю\nСовет по дизайну: Текст внизу"
        result = _parse_response(raw)
        assert result["translation"]  # должен найти перевод
        assert result["original"]
        assert result["design"]

    def test_complete_garbage(self):
        """Абсолютный мусор — не падает, возвращает пустые строки."""
        raw = "аоыврпаовпрпаоврпаов"
        result = _parse_response(raw)
        # Должен вернуть что-то в translation как последний fallback
        assert isinstance(result["original"], str)
        assert isinstance(result["translation"], str)
        assert isinstance(result["design"], str)

    def test_empty_string(self):
        """Пустая строка."""
        result = _parse_response("")
        assert isinstance(result["original"], str)
        assert isinstance(result["translation"], str)

    def test_partial_fields(self):
        """Неполный JSON (пропущен design)."""
        raw = '{"original": "A", "translation": "Б"}'
        result = _parse_response(raw)
        assert result["original"] == "A"
        assert result["translation"] == "Б"
        assert result["design"] == ""  # design отсутствует, но не падает


class TestParseResponseEdgeCases:
    def test_none_as_string(self):
        """JSON с null значением."""
        raw = '{"original": null, "translation": "Б", "design": "В"}'
        result = _parse_response(raw)
        assert result["original"] == ""
        assert result["translation"] == "Б"

    def test_unicode_text(self):
        """Юникодные символы."""
        raw = '{"original": "🔥🏀", "translation": "Огонь!", "design": "👍"}'
        result = _parse_response(raw)
        assert result["translation"] == "Огонь!"

    def test_multiline_translation(self):
        """Перевод с переносами строк."""
        raw = '{"original": "Step 1. Step 2.", "translation": "Шаг 1.\nШаг 2.", "design": "ok"}'
        result = _parse_response(raw)
        assert "Шаг 1." in result["translation"]