from __future__ import annotations

from typing import Any, Dict

from pokemon_valuator.components.simple_price_lookup import SimplePriceLookup

# PriceCharting public scrape (your working module)
from pokemon_valuator.integrations.pricecharting_public import (
    search_pricecharting_page,
    scrape_pricecharting_psa_prices,
)


def _extract_market_from_pokemontcg_prices(tcg_prices: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pick the best available price variant from PokemonTCG's tcgplayer.prices.

    We prefer:
      directLow > market > mid > low

    We also sanitize:
      - treat high >= 100 as junk for most cards (PokemonTCG often sets 999.0)
    """
    if not isinstance(tcg_prices, dict) or not tcg_prices:
        return {}

    def to_f(x):
        try:
            return float(x)
        except Exception:
            return None

    def sanitize(obj: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(obj)
        high = to_f(out.get("high"))
        if high is not None and high >= 100:
            out["high"] = None
        return out

    def score_variant(obj: Dict[str, Any]) -> float:
        direct_low = to_f(obj.get("directLow"))
        market = to_f(obj.get("market"))
        mid = to_f(obj.get("mid"))
        low = to_f(obj.get("low"))
        best = direct_low or market or mid or low
        return best if best is not None else -1.0

    best_variant = None
    best_score = -1.0
    best_prices = None

    for variant, obj in tcg_prices.items():
        if not isinstance(obj, dict):
            continue
        sc = score_variant(obj)
        if sc > best_score:
            best_score = sc
            best_variant = variant
            best_prices = sanitize(obj)

    if best_variant is None or best_prices is None:
        return {}

    best_price = (
        to_f(best_prices.get("directLow"))
        or to_f(best_prices.get("market"))
        or to_f(best_prices.get("mid"))
        or to_f(best_prices.get("low"))
    )

    return {
        "source": "pokemontcg_tcgplayer",
        "variant": best_variant,
        "best_price": best_price,
        "prices": best_prices,
    }


def _get_psa_prices_from_pricecharting(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve a PriceCharting page from (card_name, set_name, card_number),
    then scrape graded prices from the public HTML.

    Returns a dict with status + prices, never raises.
    """
    card_name = payload.get("card_name")
    set_name = payload.get("set_name")
    card_number = payload.get("card_number")

    if not (card_name and set_name and card_number):
        return {
            "status": "missing_metadata",
            "source": "pricecharting_public",
            "message": "Need card_name, set_name, and card_number to resolve PriceCharting page.",
        }

    try:
        res = search_pricecharting_page(
            card_name=str(card_name),
            set_name=str(set_name),
            card_number=str(card_number),
        )
        if not res or not res.url:
            return {
                "status": "not_found",
                "source": "pricecharting_public",
                "message": "PriceCharting search did not return a page.",
            }

        out = scrape_pricecharting_psa_prices(url=res.url)
        out["source"] = "pricecharting_public"
        return out

    except Exception as e:
        return {
            "status": "error",
            "source": "pricecharting_public",
            "message": str(e),
        }


def get_prices_for_card_payload(payload: Dict[str, Any], *, config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """
    Returns a single pricing dict, always including:
      - market price (from cache OR PokemonTCG fallback OR PriceTracker if available)
      - psa_prices (from PriceCharting public scrape; best-effort)
    """
    card_id = payload.get("card_id")
    if not card_id:
        return {"status": "missing_card_id", "message": "No card_id in payload"}

    lookup = SimplePriceLookup(config_path=config_path)

    # Always compute PSA prices (best-effort). Never blocks market price.
    psa_prices = _get_psa_prices_from_pricecharting(payload)

    # 1) cache-first (your local pricing DB)
    cached = lookup.get_prices(card_id=card_id, tcgplayer_id=None)
    if cached.get("status") == "success":
        out = dict(cached)
        out["psa_prices"] = psa_prices
        return out

    # --- Prefer PokemonTCG fallback for market price (no extra APIs) ---
    dbg = payload.get("debug") or {}
    api_dbg = (dbg.get("api") or {}) if isinstance(dbg, dict) else {}

    tcg_prices = api_dbg.get("tcgplayer_prices") or {}
    cm_prices = api_dbg.get("cardmarket_prices") or {}

    fallback = _extract_market_from_pokemontcg_prices(tcg_prices)
    if fallback:
        out = {
            "status": "success_fallback",
            "card_id": card_id,
            "message": "Using PokémonTCG tcgplayer.prices fallback (no tcgplayer_id needed).",
            "market_fallback": fallback,
            "cardmarket_fallback": cm_prices if cm_prices else None,
            "psa_prices": psa_prices,
        }
        return out

    # 2) tcgplayer_id from payload if present → try PriceTracker (optional)
    tcgplayer_id = payload.get("tcgplayer_id")
    try:
        tcgplayer_id = int(tcgplayer_id) if tcgplayer_id is not None else None
    except Exception:
        tcgplayer_id = None

    if tcgplayer_id is not None:
        pt = lookup.get_prices(card_id=card_id, tcgplayer_id=tcgplayer_id)
        out = dict(pt)
        out["psa_prices"] = psa_prices
        return out

    # 3) Nothing worked for market price
    out = {
        "status": "missing_market_price",
        "card_id": card_id,
        "message": "No PokemonTCG price blocks available and no tcgplayer_id available for PokemonPriceTracker.",
        "psa_prices": psa_prices,
    }
    return out