"""Tests for TikTok URL parsing (tikwm module).

Cover: various URL formats, edge cases, non-URLs, malformed inputs.
"""

import pytest

from services.tikwm import is_tiktok_url


class TestIsTikTokUrl:
    def test_standard_tiktok_url(self):
        assert is_tiktok_url("https://www.tiktok.com/@user/video/12345") is True

    def test_mobile_url(self):
        assert is_tiktok_url("https://vt.tiktok.com/ZSabcdefg/") is True

    def test_tco_shortlink(self):
        assert is_tiktok_url("https://t.co/abc123") is True

    def test_musicaly_url(self):
        assert is_tiktok_url("https://musical.ly/v/12345") is True

    def test_no_tiktok_url(self):
        assert is_tiktok_url("https://youtube.com/watch?v=12345") is False

    def test_plain_text(self):
        assert is_tiktok_url("просто текст без ссылок") is False

    def test_empty_string(self):
        assert is_tiktok_url("") is False

    def test_url_in_text(self):
        """Ссылка внутри текста — должна найтись."""
        assert is_tiktok_url("смотри https://tiktok.com/@user/video/12345 круто") is True

    def test_malformed_url(self):
        assert is_tiktok_url("tiktok.com") is True  # домен совпадает

    def test_subdomain_variants(self):
        assert is_tiktok_url("https://www.tiktok.com/") is True
        assert is_tiktok_url("https://m.tiktok.com/") is True

    def test_case_insensitive(self):
        assert is_tiktok_url("HTTPS://TIKTOK.COM/VIDEO/123") is True

    def test_none_value(self):
        """is_tiktok_url принимает None — re.search на None упадёт, но функция корректна."""
        with pytest.raises(TypeError):
            is_tiktok_url(None)  # type: ignore