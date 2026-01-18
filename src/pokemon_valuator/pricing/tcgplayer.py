from __future__ import annotations

from typing import Any, Dict, Optional

from .models import PricePoint, RawPricing


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def extract_raw_pricing_from_card(card: Dict[str, Any]) -> Optional[RawPricing]:
    tcg = card.get("tcgplayer") or {}
    prices = tcg.get("prices") or {}
    if not isinstance(prices, dict) or not prices:
        return None

    by_variant: Dict[str, PricePoint] = {}

    for variant, pdata in prices.items():
        if not isinstance(pdata, dict):
            continue

        by_variant[variant] = PricePoint(
            low=_to_float(pdata.get("low")),
            mid=_to_float(pdata.get("mid")),
            high=_to_float(pdata.get("high")),
            market=_to_float(pdata.get("market")),
            directLow=_to_float(pdata.get("directLow")),
        )

    if not by_variant:
        return None

    return RawPricing(
        source="tcgplayer",
        currency="USD",
        by_variant=by_variant,
    )