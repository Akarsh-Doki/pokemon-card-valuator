from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from pokemon_valuator.components.yolo_region_detector import YOLORegionDetector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--outdir", default="debug_yolo")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--pad", type=int, default=12)
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    CLASS_REMAP = {
        "set_symbol": "title",
        "title": "card_number",
        "card_number": "set_symbol",
    }

    det = YOLORegionDetector(
        args.weights,
        conf=args.conf,
        iou=args.iou,
        class_remap=CLASS_REMAP,
    )

    img = ImageOps.exif_transpose(Image.open(args.image)).convert("RGB")
    dets = det.detect(img)

    print("\n=== YOLO DETECTIONS ===")
    if not dets:
        print("(none)")
        return

    for k, box in dets.items():
        # box has xyxy + conf + cls_name (based on your previous debug)
        print(f"{k}: xyxy={box.xyxy} conf={box.conf:.3f} cls_name={box.cls_name}")

    # Draw annotated image
    ann = img.copy()
    draw = ImageDraw.Draw(ann)

    # basic font fallback
    try:
        font = ImageFont.truetype("Arial.ttf", 24)
    except Exception:
        font = ImageFont.load_default()

    for k, box in dets.items():
        x1, y1, x2, y2 = box.xyxy
        draw.rectangle([x1, y1, x2, y2], outline=(255, 0, 0), width=4)
        draw.text((x1 + 5, y1 + 5), f"{k} {box.conf:.2f}", fill=(255, 0, 0), font=font)

        crop = det.crop(img, box, pad=args.pad)
        crop_fp = outdir / f"crop_{k}.png"
        crop.save(crop_fp)
        print(f"Saved crop: {crop_fp}")

    ann_fp = outdir / "out_annotated.png"
    ann.save(ann_fp)
    print(f"\nSaved annotated: {ann_fp}\n")

if __name__ == "__main__":
    main()