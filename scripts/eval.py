from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter
from difflib import SequenceMatcher
import statistics
from html import unescape


# Normalization helpers
_SMART_QUOTES = {
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
}
def _fix_quotes(s: str) -> str:
    for a, b in _SMART_QUOTES.items():
        s = s.replace(a, b)
    return s


def _clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = unescape(str(s))
    s = _fix_quotes(s)
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _norm_text(s: Optional[str]) -> str:
    return _clean_text(s).lower()

def _norm_number(s: Optional[str]) -> str:
    """
    Normalizes card number strings so these compare equal:
      - "052/167" == "52/167"
      - " 109/182 " == "109/182"
      - "130" stays "130"
    """
    raw = _clean_text(s)
    if not raw:
        return ""

    # fraction like 052/167
    m = re.fullmatch(r"0*(\d+)\s*/\s*0*(\d+)", raw)
    if m:
        a = int(m.group(1))
        b = int(m.group(2))
        return f"{a}/{b}"

    # simple number like 130
    m2 = re.fullmatch(r"0*(\d+)", raw)
    if m2:
        return str(int(m2.group(1)))

    return raw.lower()

def _safe(x: Any) -> str:
    return "" if x is None else str(x)


def _pct(n: int, d: int) -> float:
    return 0.0 if d <= 0 else (100.0 * float(n) / float(d))


def _sim(a: str, b: str) -> float:
    a2 = _norm_text(a)
    b2 = _norm_text(b)
    if not a2 and not b2:
        return 1.0
    if not a2 or not b2:
        return 0.0
    return SequenceMatcher(None, a2, b2).ratio()

# Labels
@dataclass
class LabelRow:
    image: str
    expected_name: str
    expected_set: str
    expected_number: str
    expected_card_id: str

def load_labels(path: Path) -> Dict[str, LabelRow]:
    if not path.exists():
        return {}

    out: Dict[str, LabelRow] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            img = _clean_text(row.get("image"))
            if not img:
                continue
            out[img] = LabelRow(
                image=img,
                expected_name=_clean_text(row.get("expected_name")),
                expected_set=_clean_text(row.get("expected_set")),
                expected_number=_clean_text(row.get("expected_number")),
                expected_card_id=_clean_text(row.get("expected_card_id")),
            )
    return out

def ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)

# PriceCharting summary helpers
def pick_psa_ladder(prices: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ungraded": prices.get("ungraded"),
        "psa7": prices.get("psa7") or prices.get("grade7"),
        "psa8": prices.get("psa8"),
        "psa9": prices.get("psa9"),
        "psa95": prices.get("psa95") or prices.get("grade95"),
        "psa10": prices.get("psa10"),
    }

def summarize_pricecharting_variants(pc: Dict[str, Any]) -> Tuple[int, int]:
    variants = pc.get("variants") or []
    have_price = 0
    for v in variants:
        p = (v.get("prices") or {})
        ladder = pick_psa_ladder(p)
        if any(ladder.values()):
            have_price += 1
    return len(variants), have_price

# CSV summary append helpers
def _pad_row(row: List[Any], ncols: int) -> List[Any]:
    row = list(row)
    if len(row) < ncols:
        row.extend([""] * (ncols - len(row)))
    return row[:ncols]

def append_summary_block_to_csv(
    out_path: Path,
    header_cols: List[str],
    summary_kv: List[Tuple[str, Any]],
    top_mismatches: Dict[str, List[Tuple[str, str, int]]],
) -> None:
    """
    Adds a summary section to the end of the existing eval_results.csv.

    - Keeps CSV column count consistent with the main table.
    - Writes:
        * a separator row
        * key/value rows
        * top mismatch tables
    """
    ncols = len(header_cols)

    with out_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)

        w.writerow(_pad_row([""], ncols))
        w.writerow(_pad_row(["==== EVAL SUMMARY (APPENDED) ===="], ncols))

        for k, v in summary_kv:
            w.writerow(_pad_row([k, v], ncols))

        for title, rows in top_mismatches.items():
            w.writerow(_pad_row([""], ncols))
            w.writerow(_pad_row([title], ncols))
            w.writerow(_pad_row(["expected", "predicted", "count"], ncols))
            for exp, pred, cnt in rows:
                w.writerow(_pad_row([exp, pred, cnt], ncols))

# YOLO proxy + remap-aware coverage
def _try_load_class_remap() -> Dict[int, int]:
    """
    Best-effort import for your class remap dict.
    Adjust this import if your project stores it elsewhere.
    """
    try:
        from pokemon_valuator.models.yolo_region_detector import CLASS_REMAP  # type: ignore
        return dict(CLASS_REMAP)
    except Exception:
        return {}

def _remap_class_id(old_id: Optional[int], class_remap: Dict[int, int]) -> Optional[int]:
    if old_id is None:
        return None
    return class_remap.get(old_id, old_id)

def yolo_proxy_and_coverage_from_debug(debug: Dict[str, Any], class_remap: Dict[int, int]) -> Dict[str, Any]:
    """
    Proxy “YOLO quality” signals from debug (no IoU metrics).
    Also computes remapped-class coverage counts if debug contains detections.

    """
    ocr = debug.get("ocr") or {}

    used_yolo_number_crop = bool(ocr.get("number_yolo") or {})

    frac = ocr.get("card_number")
    plaus = ocr.get("fraction_plaus_score")

    coverage: Dict[int, int] = {}

    det_sources: List[List[Any]] = []
    yolo = debug.get("yolo") or {}
    if isinstance(yolo, dict) and isinstance(yolo.get("detections"), list):
        det_sources.append(yolo.get("detections"))  # type: ignore
    if isinstance(debug.get("yolo_detections"), list):
        det_sources.append(debug.get("yolo_detections"))  # type: ignore

    for dets in det_sources:
        for d in dets:
            if not isinstance(d, dict):
                continue
            old = d.get("cls", d.get("class_id", d.get("class")))
            old_id = int(old) if isinstance(old, (int, float)) else None
            new_id = _remap_class_id(old_id, class_remap)
            if new_id is not None:
                coverage[new_id] = coverage.get(new_id, 0) + 1

    return {
        "used_yolo_number_crop": used_yolo_number_crop,
        "ocr_fraction": frac,
        "fraction_plaus_score": plaus,
        "remapped_class_coverage": coverage, 
    }
def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate identification + YOLO proxy coverage on ./test_images")
    ap.add_argument("--images_dir", default="test_images", help="Directory of test images (relative to repo root)")
    ap.add_argument("--labels", default="data/eval/labels.csv", help="Ground truth CSV path (relative to repo root)")
    ap.add_argument("--out", default="eval_outputs/eval_results.csv", help="Output CSV path (relative to repo root)")
    ap.add_argument("--limit", type=int, default=0, help="Optional limit for quick testing (0 = no limit)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    images_dir = (root / args.images_dir).resolve()
    labels_path = (root / args.labels).resolve()
    out_path = (root / args.out).resolve()
    src_dir = root / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    labels = load_labels(labels_path)

    if not images_dir.exists():
        print(f"ERROR: images_dir not found: {images_dir}")
        return 2

    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    images = sorted(
        [
            p
            for p in images_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in valid_exts
            and not p.name.startswith(".")
            and p.name != ".DS_Store"
        ]
    )
    if args.limit and args.limit > 0:
        images = images[: args.limit]
    class_remap = _try_load_class_remap()

    yolo_weights = str(root / "runs/yolo_regions/regions2/weights/best.pt")
    try:
        import ultralytics  # noqa: F401
    except Exception:
        yolo_weights = ""

    from pokemon_valuator.models.card_identifier import CardIdentifier

    ci = CardIdentifier(
        cards_reference_csv=str(root / "data/raw/pokemon_tcg_api/cards_reference.csv"),
        reference_images_dir=str(root / "data/raw/pokemon_tcg_api/reference_images"),
        yolo_weights_path=yolo_weights,
    )

    ensure_parent(out_path)

    n_total = 0
    n_success = 0

    n_name_ok = 0
    n_set_ok = 0
    n_num_ok = 0
    n_all_ok = 0

    yolo_used = 0
    fraction_ok = 0

    remapped_coverage_totals: Dict[int, int] = {}

    pc_any = 0
    pc_variants_total = 0
    pc_variants_with_price = 0

    per_image_secs: List[float] = []
    conf_values: List[float] = []

    status_counts = Counter()
    method_counts = Counter()

    name_mismatches = Counter() 
    set_mismatches = Counter()
    num_mismatches = Counter()

    SOFT_SIM_THRESHOLD = 0.90
    name_soft_ok = 0
    set_soft_ok = 0

    started = time.time()

    header_cols = [
        "image",
        "status",
        "confidence",
        "method",
        "pred_name",
        "pred_set",
        "pred_number",
        "pred_card_id",
        "expected_name",
        "expected_set",
        "expected_number",
        "expected_card_id",
        "name_match",
        "set_match",
        "number_match",
        "all_match",
        "yolo_used_number_crop",
        "fraction_plaus_score",
        "pricecharting_status",
        "pricecharting_variants",
        "pricecharting_variants_with_any_price",
        "runtime_s",
        "pred_conf",
    ]

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header_cols)

        for p in images:
            n_total += 1

            t0 = time.time()
            res = ci.identify(str(p))
            dt = time.time() - t0
            per_image_secs.append(dt)

            debug = res.debug or {}
            pc = debug.get("pricecharting") or {}

            pred_name = _clean_text(res.card_name)
            pred_set = _clean_text(res.set_name)
            pred_num = _clean_text(res.card_number)
            pred_id = _clean_text(res.card_id)

            # distributions
            status_counts[res.status or ""] += 1
            method_counts[res.method or ""] += 1
            if res.confidence is not None:
                try:
                    conf_values.append(float(res.confidence))
                except Exception:
                    pass

            lab = labels.get(p.name)
            exp_name = lab.expected_name if lab else ""
            exp_set = lab.expected_set if lab else ""
            exp_num = lab.expected_number if lab else ""
            exp_id = lab.expected_card_id if lab else ""

            name_match = ""
            set_match = ""
            num_match = ""
            all_match = ""

            name_ok = False
            set_ok = False
            num_ok = False
            all_ok = False

            if exp_name:
                name_ok = (_norm_text(pred_name) == _norm_text(exp_name))
                name_match = str(name_ok)
                if not name_ok:
                    name_mismatches[(exp_name, pred_name)] += 1
                if _sim(pred_name, exp_name) >= SOFT_SIM_THRESHOLD:
                    name_soft_ok += 1

            if exp_set:
                set_ok = (_norm_text(pred_set) == _norm_text(exp_set))
                set_match = str(set_ok)
                if not set_ok:
                    set_mismatches[(exp_set, pred_set)] += 1
                if _sim(pred_set, exp_set) >= SOFT_SIM_THRESHOLD:
                    set_soft_ok += 1

            if exp_num:
                num_ok = (_norm_number(pred_num) == _norm_number(exp_num))
                num_match = str(num_ok)
                if not num_ok:
                    num_mismatches[(exp_num, pred_num)] += 1

            if exp_name and exp_set and exp_num:
                all_ok = (name_ok and set_ok and num_ok)
                all_match = str(all_ok)
            if res.status == "success":
                n_success += 1

            if exp_name and name_ok:
                n_name_ok += 1
            if exp_set and set_ok:
                n_set_ok += 1
            if exp_num and num_ok:
                n_num_ok += 1
            if exp_name and exp_set and exp_num and all_ok:
                n_all_ok += 1

            yinfo = yolo_proxy_and_coverage_from_debug(debug, class_remap)
            if yinfo["used_yolo_number_crop"]:
                yolo_used += 1
            if (yinfo.get("fraction_plaus_score") or 0) >= 10 and (yinfo.get("ocr_fraction") or ""):
                fraction_ok += 1

            cov = yinfo.get("remapped_class_coverage") or {}
            for k, v in cov.items():
                remapped_coverage_totals[k] = remapped_coverage_totals.get(k, 0) + int(v)

            pc_status = pc.get("status") or ""
            if pc_status and pc_status not in ("missing_fields", "skipped_low_info", "skipped_no_candidates"):
                pc_any += 1

            vcount, vwith = summarize_pricecharting_variants(pc)
            pc_variants_total += vcount
            pc_variants_with_price += vwith

            w.writerow(
                [
                    p.name,
                    res.status,
                    f"{float(res.confidence):.6f}" if res.confidence is not None else "",
                    res.method or "",
                    pred_name,
                    pred_set,
                    pred_num,
                    pred_id,
                    exp_name,
                    exp_set,
                    exp_num,
                    exp_id,
                    name_match,
                    set_match,
                    num_match,
                    all_match,
                    str(bool(yinfo["used_yolo_number_crop"])),
                    _safe(yinfo.get("fraction_plaus_score")),
                    pc_status,
                    vcount,
                    vwith,
                    f"{dt:.3f}",
                    f"{float(res.confidence):.6f}" if res.confidence is not None else "",
                ]
            )

    elapsed = time.time() - started

    # label coverage
    n_name_labels = sum(1 for v in labels.values() if v.expected_name)
    n_set_labels = sum(1 for v in labels.values() if v.expected_set)
    n_num_labels = sum(1 for v in labels.values() if v.expected_number)
    n_all_labels = sum(1 for v in labels.values() if v.expected_name and v.expected_set and v.expected_number)

    # runtime stats
    avg_s = statistics.mean(per_image_secs) if per_image_secs else 0.0
    med_s = statistics.median(per_image_secs) if per_image_secs else 0.0
    if len(per_image_secs) >= 10:
        p90_s = statistics.quantiles(per_image_secs, n=10)[8]
    else:
        p90_s = max(per_image_secs) if per_image_secs else 0.0

    avg_conf = statistics.mean(conf_values) if conf_values else 0.0
    med_conf = statistics.median(conf_values) if conf_values else 0.0

    print("\n==================== EVAL SUMMARY ====================")
    print(f"Images evaluated: {n_total}")
    print(f"Pipeline success rate: {n_success}/{n_total} = {_pct(n_success, n_total):.2f}%")
    print(f"Elapsed: {elapsed:.2f}s (avg {elapsed/max(1,n_total):.2f}s / image)")
    print("")
    print("Ground-truth coverage (from data/eval/labels.csv):")
    print(f"  name labels:   {n_name_labels}")
    print(f"  set labels:    {n_set_labels}")
    print(f"  number labels: {n_num_labels}")
    print(f"  full labels:   {n_all_labels}")
    print("")
    if n_name_labels:
        print(f"Name accuracy:   {n_name_ok}/{n_name_labels} = {_pct(n_name_ok, n_name_labels):.2f}%")
        print(f"Name soft@{SOFT_SIM_THRESHOLD:.2f}: {name_soft_ok}/{n_name_labels} = {_pct(name_soft_ok, n_name_labels):.2f}%")
    if n_set_labels:
        print(f"Set accuracy:    {n_set_ok}/{n_set_labels} = {_pct(n_set_ok, n_set_labels):.2f}%")
        print(f"Set soft@{SOFT_SIM_THRESHOLD:.2f}: {set_soft_ok}/{n_set_labels} = {_pct(set_soft_ok, n_set_labels):.2f}%")
    if n_num_labels:
        print(f"Number accuracy: {n_num_ok}/{n_num_labels} = {_pct(n_num_ok, n_num_labels):.2f}%")
    if n_all_labels:
        print(f"Full accuracy:   {n_all_ok}/{n_all_labels} = {_pct(n_all_ok, n_all_labels):.2f}%")

    print("")
    print("YOLO/OCR proxy signals:")
    print(f"  used YOLO number crop: {yolo_used}/{n_total} = {_pct(yolo_used, n_total):.2f}%")
    print(f"  plausible fraction OCR: {fraction_ok}/{n_total} = {_pct(fraction_ok, n_total):.2f}%")

    if remapped_coverage_totals:
        print("")
        print("YOLO remapped-class coverage totals (counts across all images):")
        for cls_id in sorted(remapped_coverage_totals.keys()):
            print(f"  class {cls_id}: {remapped_coverage_totals[cls_id]} detections")

    print("")
    print("Runtime/confidence stats:")
    print(f"  avg runtime: {avg_s:.2f}s | median: {med_s:.2f}s | p90: {p90_s:.2f}s")
    print(f"  avg confidence: {avg_conf:.4f} | median: {med_conf:.4f}")

    print("")
    print("PriceCharting coverage:")
    print(f"  non-skipped PriceCharting attempts: {pc_any}/{n_total} = {_pct(pc_any, n_total):.2f}%")
    if pc_variants_total:
        print(
            f"  variants with any PSA price: {pc_variants_with_price}/{pc_variants_total} = "
            f"{_pct(pc_variants_with_price, pc_variants_total):.2f}%"
        )

    print(f"\nWrote: {out_path}")
    print("=====================================================\n")

    def _top(counter: Counter, k: int = 10) -> List[Tuple[str, str, int]]:
        out = []
        for (exp, pred), cnt in counter.most_common(k):
            out.append((exp, pred, int(cnt)))
        return out

    summary_kv = [
        ("Images evaluated", n_total),
        ("Pipeline success rate", f"{n_success}/{n_total} = {_pct(n_success, n_total):.2f}%"),
        ("Elapsed (s)", f"{elapsed:.2f}"),
        ("Avg seconds/image", f"{avg_s:.2f}"),
        ("Median seconds/image", f"{med_s:.2f}"),
        ("P90 seconds/image", f"{p90_s:.2f}"),
        ("Avg confidence", f"{avg_conf:.4f}"),
        ("Median confidence", f"{med_conf:.4f}"),

        ("Name labels", n_name_labels),
        ("Set labels", n_set_labels),
        ("Number labels", n_num_labels),
        ("Full labels", n_all_labels),

        ("Name accuracy", f"{n_name_ok}/{n_name_labels} = {_pct(n_name_ok, n_name_labels):.2f}%" if n_name_labels else ""),
        ("Set accuracy", f"{n_set_ok}/{n_set_labels} = {_pct(n_set_ok, n_set_labels):.2f}%" if n_set_labels else ""),
        ("Number accuracy", f"{n_num_ok}/{n_num_labels} = {_pct(n_num_ok, n_num_labels):.2f}%" if n_num_labels else ""),
        ("Full accuracy", f"{n_all_ok}/{n_all_labels} = {_pct(n_all_ok, n_all_labels):.2f}%" if n_all_labels else ""),

        (f"Name soft-match@{SOFT_SIM_THRESHOLD:.2f}", f"{name_soft_ok}/{n_name_labels} = {_pct(name_soft_ok, n_name_labels):.2f}%" if n_name_labels else ""),
        (f"Set soft-match@{SOFT_SIM_THRESHOLD:.2f}", f"{set_soft_ok}/{n_set_labels} = {_pct(set_soft_ok, n_set_labels):.2f}%" if n_set_labels else ""),

        ("YOLO used number crop", f"{yolo_used}/{n_total} = {_pct(yolo_used, n_total):.2f}%"),
        ("Plausible fraction OCR", f"{fraction_ok}/{n_total} = {_pct(fraction_ok, n_total):.2f}%"),

        ("Non-skipped PriceCharting attempts", f"{pc_any}/{n_total} = {_pct(pc_any, n_total):.2f}%"),
        ("PriceCharting variants w/ any PSA price", f"{pc_variants_with_price}/{pc_variants_total} = {_pct(pc_variants_with_price, pc_variants_total):.2f}%" if pc_variants_total else ""),
    ]

    for k, v in status_counts.most_common():
        summary_kv.append((f"status_count::{k}", v))
    for k, v in method_counts.most_common():
        summary_kv.append((f"method_count::{k}", v))

    top_mismatches = {
        "Top NAME mismatches": _top(name_mismatches, 10),
        "Top SET mismatches": _top(set_mismatches, 10),
        "Top NUMBER mismatches": _top(num_mismatches, 10),
    }

    append_summary_block_to_csv(out_path, header_cols, summary_kv, top_mismatches)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
