from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import requests


class RateLimitError(RuntimeError):
    pass


@dataclass
class PriceSnapshot:
    card_id: str
    tcgplayer_id: Optional[int]

    card_name: Optional[str]
    set_name: Optional[str]
    number: Optional[str]
    rarity: Optional[str]

    market_price: Optional[float]
    market_low: Optional[float]
    market_mid: Optional[float]
    market_high: Optional[float]

    psa_7_price: Optional[float]
    psa_8_price: Optional[float]
    psa_9_price: Optional[float]
    psa_10_price: Optional[float]

    source: str
    last_updated: str


class PokemonPriceTrackerClient:
    """
    PokemonPriceTracker API client.

    Correct endpoint (per docs):
      GET https://www.pokemonpricetracker.com/api/v2/cards?tcgPlayerId=...&includeBoth=true&includeEbay=true
    """
    def __init__(self, api_key: str, base_url: str = "https://www.pokemonpricetracker.com", timeout_sec: int = 20):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def fetch_card_by_tcgplayer_id(
        self,
        tcgplayer_id: int,
        include_ebay: bool = True,
        include_both: bool = True,
        include_history: bool = False,
        days: int = 30,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/api/v2/cards"
        params = {
            "tcgPlayerId": int(tcgplayer_id),
            "includeBoth": "true" if include_both else "false",
            "includeEbay": "true" if include_ebay else "false",
            "includeHistory": "true" if include_history else "false",
        }
        if include_ebay:
            params["days"] = int(days)

        r = requests.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_sec,
        )

        if r.status_code == 429:
            raise RateLimitError("PokemonPriceTracker rate limit hit (HTTP 429). Try again later.")

        r.raise_for_status()
        return r.json()

    @staticmethod
    def _to_float(x):
        try:
            return float(x) if x is not None else None
        except Exception:
            return None

    @staticmethod
    def _normalize_money(v: Optional[float], market_hint: Optional[float]) -> Optional[float]:
        """
        Some providers return cents (e.g., 19000 = $190.00).
        Heuristic:
        - if v is huge but market_hint is normal, assume cents.
        """
        if v is None:
            return None

        # If the graded number is very large (>= 5000) but market price looks like normal dollars (< 2000),
        # it's almost certainly cents.
        if market_hint is not None and market_hint < 2000 and v >= 5000:
            return round(v / 100.0, 2)

        # Otherwise treat it as dollars
        return round(v, 2)

    @staticmethod
    def _extract_psa_prices_from_sales_by_grade(sales_by_grade: dict, market_hint: Optional[float]) -> Dict[str, Optional[float]]:
        """
        salesByGrade looks like:
        {
            "psa8": {
            "medianPrice": 19000,
            "smartMarketPrice": {"price": 18050, ...},
            ...
            }
        }
        Prefer:
        1) smartMarketPrice.price (more robust)
        2) medianPrice
        3) averagePrice
        """
        def pick_price(grade_obj: dict) -> Optional[float]:
            if not grade_obj:
                return None

            smp = grade_obj.get("smartMarketPrice") or {}
            if "price" in smp and smp["price"] is not None:
                return float(smp["price"])

            if grade_obj.get("medianPrice") is not None:
                return float(grade_obj["medianPrice"])

            if grade_obj.get("averagePrice") is not None:
                return float(grade_obj["averagePrice"])

            return None

        out = {"psa7": None, "psa8": None, "psa9": None, "psa10": None}

        normalized = {}
        for k, v in (sales_by_grade or {}).items():
            kk = str(k).lower().replace(" ", "").replace("-", "")
            normalized[kk] = v
        sales_by_grade = normalized
        for g in ["psa7", "psa8", "psa9", "psa10"]:
            raw = pick_price(sales_by_grade.get(g) or {})
            out[g] = PokemonPriceTrackerClient._normalize_money(raw, market_hint)

        return out

    @staticmethod
    def normalize(api_json: Dict[str, Any], card_id: str, tcgplayer_id: Optional[int], now_iso: str) -> Optional["PriceSnapshot"]:
        """
        Convert API JSON -> PriceSnapshot.
        IMPORTANT: This API returns `data` as a dict (not a list).
        """
        data = api_json.get("data")
        if not data:
            return None

        # IMPORTANT: your response has data as a dict (not a list)
        card = data if isinstance(data, dict) else (data[0] if isinstance(data, list) and data else {})
        if not card:
            return None

        # Market pricing (this part you already have)
        prices = card.get("prices") or {}

        raw_market = prices.get("market")

        market_market = None
        market_low = None
        market_mid = None
        market_high = None

        if isinstance(raw_market, dict):
            market_market = PokemonPriceTrackerClient._to_float(raw_market.get("market"))
            market_low = PokemonPriceTrackerClient._to_float(raw_market.get("low"))
            market_mid = PokemonPriceTrackerClient._to_float(raw_market.get("mid"))
            market_high = PokemonPriceTrackerClient._to_float(raw_market.get("high"))
        else:
            # float/int/None case
            market_market = PokemonPriceTrackerClient._to_float(raw_market)

        market_hint = market_market  # used for cents-vs-dollars heuristic

        # ✅ NEW graded extraction from ebay.salesByGrade
        ebay = card.get("ebay") or {}
        sales_by_grade = ebay.get("salesByGrade") or {}
        psa = PokemonPriceTrackerClient._extract_psa_prices_from_sales_by_grade(sales_by_grade, market_hint)

        return PriceSnapshot(
            card_id=card_id,
            tcgplayer_id=tcgplayer_id,
            card_name=card.get("name"),
            set_name=card.get("setName"),
            number=str(card.get("cardNumber")) if card.get("cardNumber") is not None else None,
            rarity=card.get("rarity"),

            market_price=market_market,
            market_low=market_low,
            market_mid=market_mid,
            market_high=market_high,

            psa_7_price=psa.get("psa7"),
            psa_8_price=psa.get("psa8"),
            psa_9_price=psa.get("psa9"),
            psa_10_price=psa.get("psa10"),

            source="pokemonpricetracker_api",
            last_updated=now_iso,
        )
