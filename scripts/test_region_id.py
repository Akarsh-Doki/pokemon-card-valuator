import argparse
import json
import os
from pathlib import Path
import logging
logging.basicConfig(level=logging.INFO)

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PIL import Image, ImageOps

from pokemon_valuator.models.card_identifier import CardIdentifier
from pokemon_valuator.valuation.pricing_runtime import get_prices_for_card_payload

def resolve_yolo_weights(explicit_path: str | None, config_path: str, runs_root: str = "runs/yolo_regions"):
    def expand(p: str) -> str:
        return str(Path(os.path.expanduser(p)).resolve())

    # 1) explicit
    if explicit_path:
        p = expand(explicit_path)
        return p if os.path.exists(p) else None

    # 2) env var
    env = os.environ.get("YOLO_REGIONS_WEIGHTS")
    if env:
        p = expand(env)
        return p if os.path.exists(p) else None

    # 3) config
    if config_path and os.path.exists(config_path):
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            y = (cfg.get("yolo") or {})
            w = y.get("weights_path")
            if w:
                p = expand(w)
                return p if os.path.exists(p) else None
        except Exception:
            pass

    # 4) newest best.pt
    rr = Path(runs_root)
    if rr.exists():
        bests = list(rr.glob("**/weights/best.pt"))
        if bests:
            bests.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(bests[0].resolve())

    return None

def rotate_variants(img: Image.Image):
    # 0/90/180/270 clockwise
    return [
        ("rot0", img),
        ("rot90", img.rotate(-90, expand=True)),
        ("rot180", img.rotate(180, expand=True)),
        ("rot270", img.rotate(90, expand=True)),
    ]

def pick_best_result(results):

    def key_fn(r):
        status = getattr(r, "status", None) or (r.get("status") if isinstance(r, dict) else None)
        conf = getattr(r, "confidence", 0.0) if hasattr(r, "confidence") else (r.get("confidence", 0.0) if isinstance(r, dict) else 0.0)
        failed = 1 if status == "failed" else 0
        return (failed, -float(conf))

    return sorted(results, key=key_fn)[0]

def to_payload(obj):
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return obj

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--cards_csv", default="data/raw/pokemon_tcg_api/cards_reference.csv")
    ap.add_argument("--ref_images", default="data/raw/pokemon_tcg_api/reference_images")
    ap.add_argument("--index", default="data/processed/identification/card_index.json")
    ap.add_argument("--out", default=None, help="Optional path to write JSON.")
    ap.add_argument("--debug", action="store_true", help="Include debug block in output.")
    ap.add_argument("--yolo", default=None, help="Optional: path to YOLO weights.")
    ap.add_argument("--config", default="config/config.yaml", help="Config path for yolo.weights_path (optional).")
    ap.add_argument("--try_rotate", action="store_true", help="Try 0/90/180/270 and pick best.")
    args = ap.parse_args()

    yolo_path = resolve_yolo_weights(args.yolo, args.config)

    print("[test_region_id] YOLO weights resolved =", yolo_path)
    print("[test_region_id] YOLO enabled? ", bool(yolo_path))

    ci = CardIdentifier(
        cards_reference_csv=args.cards_csv,
        reference_images_dir=args.ref_images,
        index_path=args.index,
        yolo_weights_path=yolo_path,
    )

    base_img = ImageOps.exif_transpose(Image.open(args.image)).convert("RGB")

    results = []

    if args.try_rotate:
        for tag, im in rotate_variants(base_img):
            if hasattr(ci, "identify_pil"):
                r = ci.identify_pil(im)
            else:
                tmp = Path("data/processed/tmp")
                tmp.mkdir(parents=True, exist_ok=True)
                tmp_path = tmp / f"_tmp_{tag}.jpg"
                im.save(tmp_path, "JPEG", quality=95)
                r = ci.identify(str(tmp_path))
            results.append(r)

        result = pick_best_result(results)
    else:
        if hasattr(ci, "identify_pil"):
            result = ci.identify_pil(base_img)
        else:
            result = ci.identify(args.image)

    payload = to_payload(result)

    if isinstance(payload, dict) and payload.get("card_id"):
        payload["pricing"] = get_prices_for_card_payload(payload, config_path=args.config)
    else:
        payload["pricing"] = None

    # Clean output unless --debug (keep pricing)
    if not args.debug and isinstance(payload, dict) and "debug" in payload:
        payload = dict(payload)
        payload.pop("debug", None)

    print(json.dumps(payload, indent=2, default=str))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        print("wrote", args.out)

if __name__ == "__main__":
    main()