import os
import argparse
from pathlib import Path

from PIL import Image, ImageOps

from pokemon_valuator.components.yolo_region_detector import YOLORegionDetector
from pokemon_valuator.models.card_identifier import CardIdentifier


CLASS_REMAP = {
    "set_symbol": "title",
    "title": "card_number",
    "card_number": "set_symbol",
}

def _pick_best_rotation(detector: YOLORegionDetector, img: Image.Image):
    """
    Try 0/90/180/270 and pick the rotation that yields:
      1) most detected classes
      2) highest sum(conf) as tie-breaker
    Returns: (best_img, best_dets, best_angle)
    """
    best_img = img
    best_dets = {}
    best_angle = 0
    best_count = -1
    best_conf_sum = -1.0

    for angle in (0, 90, 180, 270):
        rimg = img if angle == 0 else img.rotate(angle, expand=True)
        dets = detector.detect(rimg)

        count = len(dets)
        conf_sum = sum(b.conf for b in dets.values())

        if (count > best_count) or (count == best_count and conf_sum > best_conf_sum):
            best_img = rimg
            best_dets = dets
            best_angle = angle
            best_count = count
            best_conf_sum = conf_sum

    return best_img, best_dets, best_angle

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--outdir", default="debug_ocr")
    ap.add_argument(
        "--try_rotate",
        action="store_true",
        help="If set, tries 0/90/180/270 rotations and picks best YOLO result.",
    )
    args = ap.parse_args()

    weights = os.environ.get("YOLO_REGIONS_WEIGHTS")
    if not weights:
        raise SystemExit("YOLO_REGIONS_WEIGHTS not set")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    img = ImageOps.exif_transpose(Image.open(args.image)).convert("RGB")

    det = YOLORegionDetector(
        weights,
        class_remap=CLASS_REMAP,
    )

    if args.try_rotate:
        img, dets, angle = _pick_best_rotation(det, img)
        print(f"[debug] picked rotation angle={angle} (try_rotate enabled)")
    else:
        dets = det.detect(img)

    print("DETECTIONS:", {k: v.xyxy for k, v in dets.items()})

    ci = CardIdentifier(
        cards_reference_csv="data/raw/pokemon_tcg_api/cards_reference.csv",
        reference_images_dir="data/raw/pokemon_tcg_api/reference_images",
        yolo_weights_path=weights,
    )

    # ---- TITLE
    if "title" in dets:
        crop = det.crop(img, dets["title"], pad=10)
        crop.save(outdir / "title.png")
        text, dbg = ci._ocr_text(crop, mode="title")
        print("TITLE OCR:", text, "| engine:", dbg.get("engine"))
    else:
        print("TITLE: missing")

    # ---- NUMBER
    if "card_number" in dets:
        crop = det.crop(img, dets["card_number"], pad=10)
        crop.save(outdir / "card_number.png")
        text, dbg = ci._ocr_text(crop, mode="number")
        print("NUMBER OCR:", text, "| engine:", dbg.get("engine"))
    else:
        print("CARD_NUMBER: missing")

    # ---- SET SYMBOL
    if "set_symbol" in dets:
        crop = det.crop(img, dets["set_symbol"], pad=10)
        crop.save(outdir / "set_symbol.png")
    else:
        print("SET_SYMBOL: missing")

    # ---- Draw boxes overlay
    boxed = det.draw(img, dets)
    boxed.save(outdir / "boxed.png")

    print(f"Saved debug images to {outdir.resolve()}")
    print("Open:", outdir / "boxed.png")

if __name__ == "__main__":
    main()