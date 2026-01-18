import csv
import os
from typing import Dict, Optional
from pokemon_valuator.valuation.types import PriceResult

class PriceCacheCSV:
    def __init__(self, path: str):
        self.path = path
        self._index: Dict[str, dict] = {}
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        self._loaded = True
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = row.get("card_id")
                if cid:
                    self._index[cid] = row

    def get(self, card_id: str) -> Optional[PriceResult]:
        self.load()
        row = self._index.get(card_id)
        if not row:
            return None

        ungraded = row.get("ungraded_market")
        psa_json = row.get("psa_by_grade_json", "{}")

        import json
        try:
            psa = json.loads(psa_json) if psa_json else {}
        except Exception:
            psa = {}

        return PriceResult(
            card_id=card_id,
            ungraded_market=float(ungraded) if ungraded not in (None, "", "null") else None,
            psa_by_grade={k: float(v) for k, v in psa.items()},
            source="cache",
        )

    def upsert(self, pr: PriceResult):
        self.load()
        import json
        self._index[pr.card_id] = {
            "card_id": pr.card_id,
            "ungraded_market": "" if pr.ungraded_market is None else pr.ungraded_market,
            "psa_by_grade_json": json.dumps(pr.psa_by_grade),
            "source": pr.source,
        }
        self._flush()

    def _flush(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fieldnames = ["card_id", "ungraded_market", "psa_by_grade_json", "source"]
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in self._index.values():
                w.writerow(row)