from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
import re
import pandas as pd
import yaml

from pokemon_valuator.integrations.pricecharting_public import (
    search_pricecharting_page,
    scrape_pricecharting_psa_prices,
)

logger = logging.getLogger(__name__)

@dataclass
class GradedSnapshot:
    card_id: str
    card_name: str
    set_name: str
    card_number: str
    url: str
    ungraded: Optional[float]
    psa8: Optional[float]
    psa9: Optional[float]
    psa10: Optional[float]
    source: str
    last_updated: str

class GradedPriceLookup:
    def __init__(self, config_path: str = "config/config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        pricing_cfg = cfg.get("pricing", {}) or {}

        self.cache_path = Path(pricing_cfg.get(
            "graded_cache_path",
            "data/processed/pricing/pricecharting_graded_cache.csv"
        ))
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        self.max_age_days = int(pricing_cfg.get("graded_cache_max_age_days", 30))
        self.sleep_sec = float(pricing_cfg.get("pricecharting_sleep_sec", 0.2))

        self._df = self._load_or_init_df()

    def _load_or_init_df(self) -> pd.DataFrame:
        cols = [
            "card_id", "card_name", "set_name", "card_number",
            "url", "ungraded", "psa8", "psa9", "psa10",
            "source", "last_updated",
        ]
        if self.cache_path.exists():
            df = pd.read_csv(self.cache_path)
            for c in cols:
                if c not in df.columns:
                    df[c] = None
            return df[cols]
        return pd.DataFrame(columns=cols)

    def _is_fresh(self, iso_ts: str) -> bool:
        try:
            dt = datetime.fromisoformat(str(iso_ts))
            return dt >= (datetime.utcnow() - timedelta(days=self.max_age_days))
        except Exception:
            return False

    def _save(self) -> None:
        self._df.to_csv(self.cache_path, index=False)

    def get_psa_prices(
        self,
        *,
        card_id: str,
        card_name: str,
        set_name: str,
        card_number: str,
    ) -> Dict[str, Any]:

        def _slugify(s: str) -> str:
            s = (s or "").strip().lower()
            s = s.replace("&", "and")
            s = re.sub(r"[^a-z0-9]+", "-", s)
            s = re.sub(r"-+", "-", s).strip("-")
            return s

        def _num_left(num: str) -> Optional[str]:
            if not num:
                return None
            left = str(num).split("/")[0].strip()
            return left or None

        def _bad_placeholder(url: str) -> bool:
            if not url:
                return True
            u = url.rstrip("/")
            return u.endswith("/game/*") or u.endswith("/product/*") or "/game/*" in u or "/product/*" in u

        hit = self._df[self._df["card_id"] == card_id]
        if not hit.empty:
            row = hit.iloc[0].to_dict()
            if row.get("last_updated") and self._is_fresh(str(row["last_updated"])):
                return {
                    "status": "success_cached",
                    "source": "pricecharting_public",
                    "url": row.get("url"),
                    "ungraded": row.get("ungraded"),
                    "psa8": row.get("psa8"),
                    "psa9": row.get("psa9"),
                    "psa10": row.get("psa10"),
                    "cache_file": str(self.cache_path),
                    "last_updated": row.get("last_updated"),
                }

        num_only = _num_left(card_number)
        if not (card_name and set_name and num_only):
            return {
                "status": "missing_metadata",
                "source": "pricecharting_public",
                "message": "Need card_name, set_name, and card_number (like 52/167) for PSA scrape.",
            }

        set_slug = "pokemon-" + _slugify(set_name)
        card_slug = _slugify(card_name)
        direct_url = f"https://www.pricecharting.com/game/{set_slug}/{card_slug}-{num_only}"

        scraped = scrape_pricecharting_psa_prices(
            url=direct_url,
            sleep_sec=self.sleep_sec,
        )
        if scraped.get("status") == "success":
            now_iso = datetime.utcnow().isoformat()
            snap = GradedSnapshot(
                card_id=card_id,
                card_name=card_name,
                set_name=set_name,
                card_number=card_number,
                url=direct_url,
                ungraded=scraped.get("ungraded"),
                psa8=scraped.get("psa8"),
                psa9=scraped.get("psa9"),
                psa10=scraped.get("psa10"),
                source="pricecharting_public",
                last_updated=now_iso,
            )

            self._df = self._df[self._df["card_id"] != card_id]
            self._df = pd.concat([self._df, pd.DataFrame([asdict(snap)])], ignore_index=True)
            self._save()

            return {
                "status": "success",
                "source": "pricecharting_public",
                "url": direct_url,
                "ungraded": snap.ungraded,
                "psa8": snap.psa8,
                "psa9": snap.psa9,
                "psa10": snap.psa10,
                "cache_file": str(self.cache_path),
                "last_updated": now_iso,
                "resolved_by": "direct_url",
            }

        res = search_pricecharting_page(
            card_name=card_name,
            set_name=set_name,
            card_number=card_number,
        )
        if res is None or not getattr(res, "url", None) or _bad_placeholder(res.url):
            return {
                "status": "not_found",
                "source": "pricecharting_public",
                "message": "Could not find a valid PriceCharting page for this card (search returned placeholder or nothing).",
                "attempted_direct_url": direct_url,
                "search_url": getattr(res, "url", None) if res else None,
            }

        scraped2 = scrape_pricecharting_psa_prices(
            url=res.url,
            sleep_sec=self.sleep_sec,
        )
        if scraped2.get("status") != "success":
            return {
                "status": scraped2.get("status", "parse_failed"),
                "source": "pricecharting_public",
                "url": res.url,
                "message": "Could not extract graded prices from the PriceCharting page.",
                "attempted_direct_url": direct_url,
                "resolved_by": "search",
            }

        now_iso = datetime.utcnow().isoformat()
        snap = GradedSnapshot(
            card_id=card_id,
            card_name=card_name,
            set_name=set_name,
            card_number=card_number,
            url=res.url,
            ungraded=scraped2.get("ungraded"),
            psa8=scraped2.get("psa8"),
            psa9=scraped2.get("psa9"),
            psa10=scraped2.get("psa10"),
            source="pricecharting_public",
            last_updated=now_iso,
        )

        self._df = self._df[self._df["card_id"] != card_id]
        self._df = pd.concat([self._df, pd.DataFrame([asdict(snap)])], ignore_index=True)
        self._save()

        return {
            "status": "success",
            "source": "pricecharting_public",
            "url": res.url,
            "ungraded": snap.ungraded,
            "psa8": snap.psa8,
            "psa9": snap.psa9,
            "psa10": snap.psa10,
            "cache_file": str(self.cache_path),
            "last_updated": now_iso,
            "resolved_by": "search",
            "attempted_direct_url": direct_url,
        }