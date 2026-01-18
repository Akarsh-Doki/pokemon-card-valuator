from __future__ import annotations

from pathlib import Path
import yaml

from src.pokemon_valuator.models.card_identifier import CardIdentifier


def main(config_path: str = "config/config.yaml"):
    cfg = yaml.safe_load(open(config_path, "r", encoding="utf-8"))
    cards_csv = Path(cfg["raw_data"]) / "pokemon_tcg_api" / "cards_reference.csv"
    images_dir = Path(cfg["raw_data"]) / "pokemon_tcg_api" / "reference_images"
    out_index = Path(cfg["processed_data"]) / "identification" / "card_index.json"
    out_index.parent.mkdir(parents=True, exist_ok=True)

    identifier = CardIdentifier(
        cards_reference_csv=str(cards_csv),
        reference_images_dir=str(images_dir),
        index_path=str(out_index),
    )
    identifier.build_reference_index()
    print(f"✅ Wrote index: {out_index}")


if __name__ == "__main__":
    main()
