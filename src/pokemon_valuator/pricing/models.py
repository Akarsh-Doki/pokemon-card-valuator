from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class PricePoint:
    low: Optional[float] = None
    mid: Optional[float] = None
    high: Optional[float] = None
    market: Optional[float] = None
    directLow: Optional[float] = None


@dataclass(frozen=True)
class RawPricing:
    source: str  # "tcgplayer" | "cardmarket" | "none"
    currency: str  # "USD" typically for tcgplayer
    by_variant: Dict[str, PricePoint]  # e.g. "normal", "holofoil", "reverseHolofoil"

@dataclass(frozen=True)
class GradedPricing:
    source: str  # "local_comps" for now
    currency: str
    by_grade: Dict[str, Optional[float]]  # "PSA_8", "PSA_9", "PSA_10"

@dataclass(frozen=True)
class PricingBundle:
    raw: Optional[RawPricing] = None
    graded: Optional[GradedPricing] = None