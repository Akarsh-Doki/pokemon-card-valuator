from __future__ import annotations
import os
import csv
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml
import logging

from pokemon_valuator.integrations.pokemonpricetracker_api import (
    PokemonPriceTrackerClient,
    RateLimitError,
    PriceSnapshot,
)
logger = logging.getLogger(__name__)

class SimplePriceLookup:
    def __init__(self, config_path: str = "config/config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self.cache_path = Path(self.config.get("pricing_db_path", "data/processed/pricing/complete_price_database.csv"))
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        self._df = self._load_or_init_df()

        self.enable_on_demand_api = bool(self.config.get("enable_on_demand_pricing_api", True))
        api_key = os.environ.get("POKEMONPRICETRACKER_API_KEY") or self.config.get("pokemonpricetracker_api_key")
        self.api_client = None
        if self.enable_on_demand_api and api_key:
            self.api_client = PokemonPriceTrackerClient(
                api_key=api_key,
                base_url=self.config.get("pokemonpricetracker_base_url", "https://www.pokemonpricetracker.com"),
            )

    def _load_or_init_df(self) -> pd.DataFrame:
        cols = [
            "card_id", "tcgplayer_id",
            "card_name", "set_name", "number", "rarity",
            "market_price", "market_low", "market_mid", "market_high",
            "psa_7_price", "psa_8_price", "psa_9_price", "psa_10_price",
            "source", "last_updated",
        ]
        if self.cache_path.exists():
            df = pd.read_csv(self.cache_path)
            for c in cols:
                if c not in df.columns:
                    df[c] = None
            return df[cols]
        return pd.DataFrame(columns=cols)

    def _append_snapshot(self, snap: PriceSnapshot) -> None:
        row = asdict(snap)
        self._df = pd.concat([self._df, pd.DataFrame([row])], ignore_index=True)
        self._df = self._df.sort_values("last_updated").drop_duplicates(subset=["card_id"], keep="last")
        self._df.to_csv(self.cache_path, index=False)
    
    def _resolve_tcgplayer_id_from_pricecharting(
        self,
        *,
        card_name: str,
        set_name: str,
        card_number: str,
    ) -> Optional[int]:
        num_only = str(card_number).split("/")[0].strip() if card_number else ""
        return self._pricecharting_search_tcgplayer_id(
            card_name=str(card_name).strip(),
            set_name=str(set_name).strip(),
            card_number=num_only,
        )
    def _pricecharting_search_tcgplayer_id(self, *, card_name: str, set_name: str, card_number: str) -> Optional[int]:
        import re
        import requests

        tcg_re = re.compile(r"TCGPlayer ID:\s*([0-9]+)", re.IGNORECASE)
        link_re = re.compile(r'href="(/(?:game|product)/[^"]+)"', re.IGNORECASE)
        queries = [
            f"{card_name} {card_number} {set_name}".strip(),
            f"{card_name} #{card_number} {set_name}".strip(),
            f"{card_name} {set_name} {card_number}".strip(),
        ]

        search_url = "https://www.pricecharting.com/search-products"

        for q in queries:
            logger.info(f"[pricecharting] search query: {q}")
            r = requests.get(
                search_url,
                params={"type": "prices", "q": q},
                timeout=20,
                headers={"User-Agent": "pokemon-card-valuator/1.0"},
            )

            if r.status_code >= 400:
                logger.warning(f"[pricecharting] search HTTP {r.status_code}")
                continue

            html = r.text or ""

            m = link_re.search(html)
            if not m:
                logger.warning("[pricecharting] no /game/ or /product/ link found in search results")
                continue

            page_url = "https://www.pricecharting.com" + m.group(1)
            logger.info(f"[pricecharting] first result: {page_url}")

            r2 = requests.get(page_url, timeout=20, headers={"User-Agent": "pokemon-card-valuator/1.0"})
            if r2.status_code >= 400:
                logger.warning(f"[pricecharting] result page HTTP {r2.status_code}")
                continue

            html2 = r2.text or ""
            m2 = tcg_re.search(html2)
            if not m2:
                logger.warning("[pricecharting] TCGPlayer ID not found on result page")
                continue

            try:
                tcgplayer_id = int(m2.group(1))
                logger.info(f"[pricecharting] resolved tcgplayer_id = {tcgplayer_id}")
                return tcgplayer_id
            except Exception:
                logger.warning("[pricecharting] failed to parse tcgplayer_id")
                continue

        return None


    def _format_row(self, row: pd.Series) -> Dict[str, Any]:
        def f(x):
            try:
                return float(x) if pd.notna(x) else None
            except Exception:
                return None

        return {
            "status": "success",
            "card_id": row.get("card_id"),
            "tcgplayer_id": int(row["tcgplayer_id"]) if pd.notna(row.get("tcgplayer_id")) else None,
            "card_name": row.get("card_name"),
            "set_name": row.get("set_name"),
            "number": row.get("number"),
            "rarity": row.get("rarity"),
            "market": {
                "market": f(row.get("market_price")),
                "low": f(row.get("market_low")),
                "mid": f(row.get("market_mid")),
                "high": f(row.get("market_high")),
            },
            "graded": {
                "psa7": f(row.get("psa_7_price")),
                "psa8": f(row.get("psa_8_price")),
                "psa9": f(row.get("psa_9_price")),
                "psa10": f(row.get("psa_10_price")),
            },
            "source": row.get("source"),
            "last_updated": row.get("last_updated"),
            "cache_file": str(self.cache_path),
        }

    def get_prices(self, card_id: str, tcgplayer_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Main runtime call:
        1) return cached snapshot if exists
        2) else call API ONCE (if enabled + key exists + tcgplayer_id provided)
        3) append result to CSV (cache)
        4) return result
        """
        hit = self._df[self._df["card_id"] == card_id]
        if not hit.empty:
            return self._format_row(hit.iloc[0])

        if not self.enable_on_demand_api:
            return {
                "status": "missing_in_db",
                "card_id": card_id,
                "message": "Card not in local pricing DB and on-demand API disabled.",
            }

        if self.api_client is None:
            return {
                "status": "missing_api_key",
                "card_id": card_id,
                "message": "Card not in local pricing DB and pricing API key is not configured.",
            }

        if tcgplayer_id is None:
            return {
                "status": "missing_tcgplayer_id",
                "card_id": card_id,
                "message": "Need tcgplayer_id to fetch from PokemonPriceTracker. Could not resolve it automatically.",
            }
           
        now_iso = datetime.utcnow().isoformat()
        try:
            api_json = self.api_client.fetch_card_by_tcgplayer_id(int(tcgplayer_id))
            snap = self.api_client.normalize(api_json, card_id=card_id, tcgplayer_id=int(tcgplayer_id), now_iso=now_iso)
            if snap is None:
                return {"status": "no_price_data", "card_id": card_id, "message": "Pricing API returned no data."}

            self._append_snapshot(snap)

            hit = self._df[self._df["card_id"] == card_id]
            return self._format_row(hit.iloc[0])

        except RateLimitError as e:
            logger.warning(str(e))
            return {"status": "rate_limited", "card_id": card_id, "message": str(e)}

        except Exception as e:
            logger.exception("Pricing API error")
            return {"status": "api_error", "card_id": card_id, "message": str(e)}