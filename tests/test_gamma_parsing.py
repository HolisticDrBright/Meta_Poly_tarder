"""Tests for Gamma API response parsing robustness."""

import pytest
from backend.data_layer.gamma_client import GammaMarket


class TestGammaMarketParsing:
    def test_json_string_prices(self):
        """outcomePrices as JSON string (most common format)."""
        data = {
            "id": "123",
            "conditionId": "cond-123",
            "question": "Test?",
            "outcomePrices": '["0.35","0.65"]',
            "liquidity": "50000",
            "volume": "10000",
            "volume24hr": "5000",
            "active": True,
            "closed": False,
        }
        m = GammaMarket.from_api(data)
        assert m.yes_price == pytest.approx(0.35, abs=0.001)
        assert m.no_price == pytest.approx(0.65, abs=0.001)

    def test_list_prices(self):
        """outcomePrices already a list."""
        data = {
            "id": "124",
            "conditionId": "cond-124",
            "question": "Test?",
            "outcomePrices": [0.42, 0.58],
            "liquidity": "30000",
        }
        m = GammaMarket.from_api(data)
        assert m.yes_price == pytest.approx(0.42, abs=0.001)
        assert m.no_price == pytest.approx(0.58, abs=0.001)

    def test_list_string_prices(self):
        """outcomePrices as list of strings."""
        data = {
            "id": "125",
            "question": "Test?",
            "outcomePrices": ["0.70", "0.30"],
            "liquidity": "20000",
        }
        m = GammaMarket.from_api(data)
        assert m.yes_price == pytest.approx(0.70, abs=0.001)

    def test_missing_prices_defaults_to_50(self):
        """Missing outcomePrices defaults to 0.5."""
        data = {"id": "126", "question": "Test?", "liquidity": "10000"}
        m = GammaMarket.from_api(data)
        assert m.yes_price == pytest.approx(0.5)
        assert m.no_price == pytest.approx(0.5)

    def test_malformed_json_string(self):
        """Malformed JSON string falls back to 0.5."""
        data = {
            "id": "127",
            "question": "Test?",
            "outcomePrices": "not valid json",
            "liquidity": "10000",
        }
        m = GammaMarket.from_api(data)
        assert m.yes_price == pytest.approx(0.5)

    def test_numeric_string_prices(self):
        """outcomePrices as JSON string with unquoted numbers."""
        data = {
            "id": "128",
            "question": "Test?",
            "outcomePrices": "[0.28,0.72]",
            "liquidity": "40000",
        }
        m = GammaMarket.from_api(data)
        assert m.yes_price == pytest.approx(0.28, abs=0.001)

    def test_end_date_parsing(self):
        """Various endDate formats."""
        data = {
            "id": "129",
            "question": "Test?",
            "outcomePrices": [0.5, 0.5],
            "endDate": "2026-06-15T00:00:00Z",
            "liquidity": "10000",
        }
        m = GammaMarket.from_api(data)
        assert m.end_date is not None
        assert m.end_date.year == 2026

    def test_missing_end_date(self):
        data = {"id": "130", "question": "Test?", "liquidity": "10000"}
        m = GammaMarket.from_api(data)
        assert m.end_date is None

    def test_best_bid_ask_fallback(self):
        """bestBid/bestAsk missing → fall back to yes_price."""
        data = {
            "id": "131",
            "question": "Test?",
            "outcomePrices": [0.40, 0.60],
            "liquidity": "10000",
        }
        m = GammaMarket.from_api(data)
        assert m.best_bid == pytest.approx(0.40, abs=0.01)
        assert m.best_ask == pytest.approx(0.40, abs=0.01)

    def test_all_fields_populated(self):
        """Full response with all fields."""
        data = {
            "id": "full-market",
            "conditionId": "0xabc",
            "question": "Will Bitcoin reach $200k?",
            "groupItemTitle": "Crypto",
            "outcomePrices": '["0.15","0.85"]',
            "bestBid": "0.14",
            "bestAsk": "0.16",
            "endDate": "2026-12-31T23:59:59Z",
            "active": True,
            "closed": False,
            "liquidity": "150000",
            "volume": "500000",
            "volume24hr": "25000",
            "outcomes": '["Yes","No"]',
        }
        m = GammaMarket.from_api(data)
        assert m.id == "full-market"
        assert m.condition_id == "0xabc"
        assert m.yes_price == pytest.approx(0.15, abs=0.01)
        assert m.best_bid == pytest.approx(0.14, abs=0.01)
        assert m.best_ask == pytest.approx(0.16, abs=0.01)
        assert m.liquidity == 150000
        assert m.category == "Crypto"
