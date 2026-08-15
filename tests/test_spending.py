"""Tests for spending tracker module (SQLite-backed).

Tests are isolated: conftest resets state between tests.
All spending functions are now async.
"""

import pytest

from services.spending import (
    calculate_cost,
    get_daily_report,
    is_budget_exceeded,
    track_usage,
)


class TestCalculateCost:
    """calculate_cost — чистая синхронная функция, не требует async."""

    def test_gemini_flash_known(self):
        """google/gemini-2.5-flash: $0.30/M in, $2.50/M out."""
        cost = calculate_cost(1000, 500, "google/gemini-2.5-flash")
        expected = (1000 * 0.30 + 500 * 2.50) / 1_000_000
        assert cost == expected

    def test_unknown_model_uses_default(self):
        """Неизвестная модель — fallback на среднюю цену."""
        cost = calculate_cost(1000, 500, "unknown/model")
        # Default: $1.0/M in, $3.0/M out
        expected = (1000 * 1.0 + 500 * 3.0) / 1_000_000
        assert cost == expected

    def test_zero_tokens(self):
        cost = calculate_cost(0, 0, "google/gemini-2.5-flash")
        assert cost == 0.0


class TestTrackUsage:
    async def test_track_returns_cost(self):
        result = await track_usage(1000, 500, "google/gemini-2.5-flash")
        assert "cost_usd" in result
        assert result["prompt_tokens"] == 1000
        assert result["completion_tokens"] == 500
        assert result["cost_usd"] > 0

    async def test_track_daily_accumulates(self):
        await track_usage(1000, 500, "google/gemini-2.5-flash")
        await track_usage(2000, 300, "google/gemini-2.5-flash")
        assert await is_budget_exceeded(100_000) is False


class TestIsBudgetExceeded:
    async def test_zero_budget_no_limit(self):
        """0 = без лимита, никогда не превышен."""
        assert await is_budget_exceeded(0) is False

    async def test_negative_budget_no_limit(self):
        assert await is_budget_exceeded(-1) is False

    async def test_exceeded_after_usage(self):
        await track_usage(100_000, 50_000, "google/gemini-2.5-flash")
        assert await is_budget_exceeded(10) is True


class TestGetDailyReport:
    async def test_report_no_spending(self):
        report = await get_daily_report()
        assert "не было" in report

    async def test_report_after_tracking(self):
        await track_usage(1000, 500, "google/gemini-2.5-flash")
        report = await get_daily_report()
        assert "запросов" in report
        assert "токенов" in report