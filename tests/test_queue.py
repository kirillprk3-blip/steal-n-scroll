"""Tests for queue module (SQLite-backed).

Cover: add new/duplicate/processed, take N, count, clear.
"""

import pytest

from services.dedup import mark_processed
from services.queue import add, clear, count, take


class TestAdd:
    async def test_add_new_url(self):
        added, note = await add("https://tiktok.com/@u/v/new1")
        assert added is True
        assert "очереди" in note

    async def test_add_duplicate(self):
        await add("https://tiktok.com/@u/v/dup_test")
        added, note = await add("https://tiktok.com/@u/v/dup_test")
        assert added is False
        assert "уже в очереди" in note

    async def test_add_normalizes_url(self):
        await add("HTTPS://TIKTOK.COM/A")
        added, note = await add("https://tiktok.com/a")
        assert added is False  # нормализованный дубликат

    async def test_add_rejects_processed(self):
        """URL, который уже обработан ранее, не добавляется."""
        await mark_processed("https://tiktok.com/@u/v/already_done")
        added, note = await add("https://tiktok.com/@u/v/already_done")
        assert added is False
        assert "уже обрабатывали" in note

    async def test_add_with_spaces(self):
        await add("  https://tiktok.com/@u/v/spaced  ")
        added, note = await add("https://tiktok.com/@u/v/spaced")
        assert added is False


class TestTake:
    async def test_take_returns_urls(self):
        await add("https://tiktok.com/1")
        await add("https://tiktok.com/2")
        urls = await take(2)
        assert len(urls) == 2
        assert "https://tiktok.com/1" in urls

    async def test_take_respects_count(self):
        await add("https://tiktok.com/a")
        await add("https://tiktok.com/b")
        await add("https://tiktok.com/c")
        urls = await take(2)
        assert len(urls) == 2
        assert await count() == 1  # один остался

    async def test_take_more_than_available(self):
        await add("https://tiktok.com/1")
        urls = await take(10)
        assert len(urls) == 1

    async def test_take_from_empty(self):
        urls = await take(5)
        assert urls == []


class TestCount:
    async def test_count_empty(self):
        assert await count() == 0

    async def test_count_after_add(self):
        await add("https://tiktok.com/1")
        assert await count() == 1
        await add("https://tiktok.com/2")
        assert await count() == 2

    async def test_count_after_take(self):
        await add("https://tiktok.com/1")
        await add("https://tiktok.com/2")
        await take(1)
        assert await count() == 1


class TestClear:
    async def test_clear_empty(self):
        n = await clear()
        assert n == 0

    async def test_clear_with_items(self):
        await add("https://tiktok.com/1")
        await add("https://tiktok.com/2")
        n = await clear()
        assert n == 2
        assert await count() == 0

    async def test_clear_then_add_works(self):
        await add("https://tiktok.com/1")
        await clear()
        assert await count() == 0
        await add("https://tiktok.com/2")
        assert await count() == 1