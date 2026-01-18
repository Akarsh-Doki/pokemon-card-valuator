from dataclasses import dataclass
from typing import Dict, Optional

@dataclass
class PriceResult:
    card_id: str
    ungraded_market: Optional[float]
    psa_by_grade: Dict[str, float]  # e.g. {"PSA10": 120.0}
    source: str  # "cache" or "pokemonpricetracker"
    currency: str = "USD"