import argparse
import os
from pathlib import Path

from PIL import Image

from src.pokemon_valuator.components.yolo_region_detector import YOLORegionDetector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--weights", default=os.environ.get("YOLO_REGIONS_WEIGHTS"))
    ap.add_argument("--out_dir", default="debug_yolo")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.5)
    args = ap.parse_args()

    if not args.weights:
        raise SystemExit("No weights provided. Pass --weights or set YOLO_REGIONS_WEIGHTS.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    img = Image.open(args.image).convert("RGB")

    det = YOLORegionDetector(args.weights, conf=args.conf, iou=args.iou)

    dets = det.detect(img)
    print("DETECTIONS:", dets)

    # overlay
    overlay = det.draw(img, dets)
    overlay_path = out_dir / "overlay.png"
    overlay.save(overlay_path)
    print("saved:", overlay_path)

    # crops
    for k in ["title", "card_number", "set_symbol"]:
        if k in dets:
            crop = det.crop(img, dets[k], pad=10)
            p = out_dir / f"{k}.png"
            crop.save(p)
            print("saved:", p)
        else:
            print("missing:", k)

if __name__ == "__main__":
    main()