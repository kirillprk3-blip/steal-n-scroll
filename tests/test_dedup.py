"""Tests for deduplication module (SQLite-backed).

Must handle: URL normalization, processed check, mark_processed persistence.
"""

from services.dedup import is_processed, mark_processed, normalize


class TestNormalize:
    def test_trim_whitespace(self):
        assert normalize("  https://tiktok.com/a  ") == "https://tiktok.com/a"

    def test_case_insensitive(self):
        assert normalize("HTTPS://TIKTOK.COM/A") == normalize("https://tiktok.com/a")

    def test_multiple_spaces(self):
        assert normalize("a   b") == "a b"

    def test_empty_string(self):
        assert normalize("") == ""


class TestIsProcessedAndMark:
    async def test_url_not_processed_initially(self):
        assert await is_processed("https://tiktok.com/@u/v/1") is False

    async def test_mark_then_check(self):
        url = "https://tiktok.com/@u/v/test123"
        assert await is_processed(url) is False
        await mark_processed(url)
        assert await is_processed(url) is True

    async def test_duplicate_mark_is_idempotent(self):
        url = "https://tiktok.com/@u/v/duplicate"
        await mark_processed(url)
        await mark_processed(url)  # второй раз не падает
        assert await is_processed(url) is True

    async def test_normalization_works_with_mark(self):
        """URL с разным регистром/пробелами считаются одним."""
        await mark_processed("https://TIKTOK.COM/@u/v/abc")
        assert await is_processed("  https://tiktok.com/@u/v/ABC  ") is True

    async def test_different_urls_independent(self):
        await mark_processed("https://tiktok.com/a")
        assert await is_processed("https://tiktok.com/b") is False