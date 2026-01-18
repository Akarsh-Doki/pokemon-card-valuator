from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import requests

TCGDEX_BASE = "https://api.tcgdex.net/v2"

@dataclass(frozen=True)
class TCGdexCardHit:
    id: str
    name: str
    localId: str
    image: Optional[str] = None


def search_cards(name: str, local_id: str, lang: str = "en", timeout_sec: int = 10) -> List[TCGdexCardHit]:
    """
    Free endpoint, no key:
      GET https://api.tcgdex.net/v2/en/cards?name=...&localId=...
    """
    url = f"{TCGDEX_BASE}/{lang}/cards"
    params = {"name": name, "localId": str(local_id)}
    r = requests.get(url, params=params, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    out: List[TCGdexCardHit] = []
    if isinstance(data, list):
        for it in data:
            if not isinstance(it, dict):
                continue
            cid = it.get("id")
            nm = it.get("name")
            lid = it.get("localId")
            if cid and nm and lid:
                out.append(TCGdexCardHit(id=str(cid), name=str(nm), localId=str(lid), image=it.get("image")))
    return out


def get_card_detail(card_id: str, lang: str = "en", timeout_sec: int = 10) -> Dict[str, Any]:
    url = f"{TCGDEX_BASE}/{lang}/cards/{card_id}"
    r = requests.get(url, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {}

def prune_tcgdex_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(detail, dict):
        return {}

    return {
        "id": detail.get("id"),
        "name": detail.get("name"),
        "localId": detail.get("localId"),
        "rarity": detail.get("rarity"),
        "set": (detail.get("set") or {}),
        "variants": detail.get("variants"),
        "variants_detailed": detail.get("variants_detailed"),
        "legal": detail.get("legal"),
        "updated": detail.get("updated"),
        "pricing": detail.get("pricing"),
        "image": detail.get("image"),
    }