from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Dict, List, Optional

import pandas as pd
import requests
import yaml
from ebaysdk.finding import Connection


@dataclass(frozen=True)
class CollectorConfig:
    max_images_total: int = 3000
    per_card_per_grade: int = 40
    grades: range = range(6, 11)
    sleep_seconds: float = 1.5


GRADE_RE = re.compile(r"\bPSA\s*[-#]?\s*(\d{1,2})\b", re.IGNORECASE)
BAD_TITLE_RE = re.compile(r"\b(reprint|proxy|custom|fake|lot|bundle)\b", re.IGNORECASE)


def load_yaml(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_psa_grade(title: str) -> Optional[int]:
    m = GRADE_RE.search(title or "")
    if not m:
        return None
    g = int(m.group(1))
    if 1 <= g <= 10:
        return g
    return None


class TrainingImageCollector:
    def __init__(self, config_path: str = "config/config.yaml", secrets_path: str = "config/secrets.yaml"):
        cfg = load_yaml(config_path)
        secrets = load_yaml(secrets_path)

        appid = secrets.get("ebay_app_id")
        if not appid:
            raise ValueError("Missing ebay_app_id in config/secrets.yaml")

        self.ebay_api = Connection(appid=appid, config_file=None)
        self.raw_dir = Path(cfg["raw_data"]) / "training_images"
        self.img_dir = self.raw_dir / "images"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.img_dir.mkdir(parents=True, exist_ok=True)

        # Curate a small high-signal set; expand later
        self.target_cards = [
            "Charizard Base Set",
            "Blastoise Base Set",
            "Venusaur Base Set",
            "Mewtwo Base Set",
            "Alakazam Base Set",
            "Gengar Fossil",
            "Dragonite Fossil",
            "Jolteon Jungle",
        ]

        self.cfg = CollectorConfig()

    def collect(self) -> pd.DataFrame:
        rows: List[Dict] = []
        downloaded = 0

        for card_name in self.target_cards:
            for grade in self.cfg.grades:
                if downloaded >= self.cfg.max_images_total:
                    break

                listings = self._search_sold_psa_listings(card_name, grade, limit=self.cfg.per_card_per_grade)
                for item in listings:
                    if downloaded >= self.cfg.max_images_total:
                        break

                    img_url = item.get("image_url")
                    if not img_url:
                        continue

                    saved = self._download_image(img_url, item_id=item["item_id"], psa_grade=item["psa_grade"])
                    if not saved:
                        continue

                    rows.append({**item, "image_path": str(saved)})
                    downloaded += 1

                sleep(self.cfg.sleep_seconds)

        df = pd.DataFrame(rows)
        out_meta = self.raw_dir / "metadata.csv"
        df.to_csv(out_meta, index=False)
        return df

    def _search_sold_psa_listings(self, card_name: str, psa_grade: int, limit: int = 40) -> List[Dict]:
        # Stronger query: include PSA grade, exclude common junk terms
        keywords = f'{card_name} PSA {psa_grade} -BGS -CGC -SGC -ACE -lot -bundle -proxy -reprint -fake'

        response = self.ebay_api.execute("findCompletedItems", {
            "keywords": keywords,
            "categoryId": "183454",  # Pokemon Individual Cards
            "itemFilter": [
                {"name": "SoldItemsOnly", "value": True},
                {"name": "MinPrice", "value": 25},
            ],
            "paginationInput": {"entriesPerPage": min(limit, 100)},
        })

        items = response.dict().get("searchResult", {}).get("item", [])
        results: List[Dict] = []

        for it in items:
            title = it.get("title", "")
            if BAD_TITLE_RE.search(title):
                continue

            detected = extract_psa_grade(title)
            if detected != psa_grade:
                continue

            results.append({
                "item_id": it.get("itemId"),
                "card_name_query": card_name,
                "psa_grade": psa_grade,
                "title": title,
                "image_url": it.get("pictureURLLarge") or it.get("galleryURL"),
                "sale_date": it.get("listingInfo", {}).get("endTime"),
            })

        return results

    def _download_image(self, url: str, item_id: str, psa_grade: int) -> Optional[Path]:
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            fp = self.img_dir / f"{item_id}_PSA{psa_grade}.jpg"
            fp.write_bytes(r.content)
            return fp
        except Exception:
            return None


if __name__ == "__main__":
    collector = TrainingImageCollector()
    df = collector.collect()
    print(df.head(5).to_string(index=False))
    print(f"Saved: {collector.raw_dir/'metadata.csv'}")
