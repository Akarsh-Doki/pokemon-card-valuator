from __future__ import annotations
# import everything needed
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Any
import glob
import os
import cv2
import numpy as np
import pandas as pd
import requests
import yaml
from PIL import Image, ImageOps, ImageFilter
from pathlib import Path

try:
    from paddleocr import PaddleOCR  # type: ignore
except Exception:
    PaddleOCR = None

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    import easyocr
except Exception:
    easyocr = None

from pokemon_valuator.components.yolo_region_detector import YOLORegionDetector, Box
from pokemon_valuator.integrations.pokemontcg_api import PokemonTCGClient
from pokemon_valuator.components.number_reader import PaddleNumberReader
from pokemon_valuator.integrations.pricecharting_public import PriceChartingResult

CARD_NO_RE = re.compile(r"(\d{1,3})\s*/\s*(\d{1,4})")
SET_CODE_RE = re.compile(r"\b([A-Z0-9]{2,4})\b")

@dataclass(frozen=True)
class IdentificationResult:
    status: str
    card_id: Optional[str]
    card_name: Optional[str]
    set_name: Optional[str]
    card_number: Optional[str]
    printed_total: Optional[int]
    tcgplayer_id: Optional[int]
    confidence: float
    method: str
    debug: Dict

class VisualEmbedder:
    def __init__(self, bins: int = 32):
        self.bins = int(bins)

    def embed(self, img: Image.Image) -> np.ndarray:
        img = img.convert("RGB").resize((224, 224))
        arr = np.array(img)
        hists = []
        for ch in range(3):
            hist, _ = np.histogram(arr[:, :, ch], bins=self.bins, range=(0, 255), density=True)
            hists.append(hist.astype(np.float32))
        v = np.concatenate(hists)
        v = v / (np.linalg.norm(v) + 1e-9)
        return v

class CardIdentifier:
    def __init__(
        self,
        cards_reference_csv: str,
        reference_images_dir: str,
        index_path: str = "data/processed/identification/card_index.json",
        config_path: str = "config/config.yaml",
        secrets_path: str = "config/secrets.yaml",
        yolo_weights_path: Optional[str] = None,
        yolo_conf: float = 0.25,
        yolo_iou: float = 0.5,
    ):
        self.cards_ref = pd.read_csv(cards_reference_csv)
        self.reference_images_dir = Path(reference_images_dir)

        self.index_path = Path(index_path)
        self.index: Optional[Dict] = None
        if self.index_path.exists():
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))

        self.embedder = VisualEmbedder()

        if not yolo_weights_path:
            yolo_weights_path = os.environ.get("YOLO_REGIONS_WEIGHTS")

        self.yolo_detector = None
        if yolo_weights_path:
            CLASS_REMAP = {
                "set_symbol": "title",
                "title": "card_number",
                "card_number": "set_symbol",
            }
            self.yolo_detector = YOLORegionDetector(
                yolo_weights_path,
                conf=yolo_conf,
                iou=yolo_iou,
                class_remap=CLASS_REMAP,
            )

        self.ocr_engine = os.environ.get("OCR_ENGINE", "auto").lower()
        self._easy_reader = None

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        api_cfg = cfg.get("pokemontcg_api", {}) or {}
        cache_cfg = cfg.get("cache", {}) or {}
        
        self.cache_ref_dir = Path(cache_cfg.get("reference_images_dir", "cache/reference_images"))
        self.cache_ref_dir.mkdir(parents=True, exist_ok=True)

        self.cache_api_dir = Path(cache_cfg.get("pokemontcg_responses_dir", "cache/pokemontcg_api_responses"))
        self.cache_api_dir.mkdir(parents=True, exist_ok=True)

        api_key = None
        sp = Path(secrets_path)
        if sp.exists():
            secrets = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
            api_key = secrets.get("pokemontcg_api_key") or secrets.get("POKEMONTCG_API_KEY")

        self.api_client = PokemonTCGClient(
            base_url=api_cfg.get("base_url", "https://api.pokemontcg.io/v2"),
            api_key=api_key,
            timeout_sec=int(api_cfg.get("timeout_sec", 8)),
            cache_dir=str(self.cache_api_dir),
        )

        self.max_candidates = int(api_cfg.get("max_candidates", 25))
        self.visual_top_k = int(api_cfg.get("visual_top_k", 10))
        self.success_threshold = float(api_cfg.get("success_threshold", 0.80))
        self.uncertain_threshold = float(api_cfg.get("uncertain_threshold", 0.70))
        self._paddle_number_reader = None
        self.setcode_ocr: Optional[str] = None

        self.download_retries = int(api_cfg.get("download_retries", 3))
        self.download_timeout = int(api_cfg.get("download_timeout_sec", 25))
        self._paddle_ocr = None
        self._last_api_debug: Dict = {}

        self.pricecharting = PriceChartingResult()
    
    DIGITISH_RE = re.compile(r"[0-9/]+")

    def _get_number_reader(self) -> Optional[PaddleNumberReader]:
        try:
            if self._paddle_number_reader is None:
                self._paddle_number_reader = PaddleNumberReader()
            return self._paddle_number_reader
        except Exception:
            return None

    def _get_paddleocr(self):
        if PaddleOCR is None:
            return None
        if self._paddle_ocr is None:
            os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
            self._paddle_ocr = PaddleOCR(lang="en")
        return self._paddle_ocr

    def _pil_to_bgr(self, img: Image.Image) -> np.ndarray:
        rgb = np.array(img.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def _keep_digitish(self, s: str) -> str:
        if not s:
            return ""
        t = "".join(ch for ch in s if ch.isdigit() or ch == "/")
        t = re.sub(r"/{2,}", "/", t)
        return t

    def _parse_old_paddleocr_result(self, result):
        texts = []
        if not result:
            return texts
        for line in result:
            for item in line:
                box = item[0]
                text = item[1][0]
                score = float(item[1][1])
                texts.append((str(text), score, box))
        return texts

    def _parse_new_paddleocr_result(self, result):
        texts = []
        if not result:
            return texts

        for item in result:
            if item is None:
                continue

            if isinstance(item, dict):
                rec_texts = item.get("rec_texts") or item.get("texts") or []
                rec_scores = item.get("rec_scores") or item.get("scores") or []
                polys = item.get("dt_polys") or item.get("dt_boxes") or item.get("boxes") or []

                if rec_texts and rec_scores and len(rec_texts) == len(rec_scores):
                    if polys and len(polys) == len(rec_texts):
                        for t, sc, poly in zip(rec_texts, rec_scores, polys):
                            texts.append((str(t), float(sc), poly))
                    else:
                        for t, sc in zip(rec_texts, rec_scores):
                            texts.append((str(t), float(sc), None))
                    continue

            for key_text, key_score, key_box in [
                ("rec_texts", "rec_scores", "dt_polys"),
                ("texts", "scores", "boxes"),
            ]:
                if hasattr(item, key_text) and hasattr(item, key_score):
                    ts = getattr(item, key_text)
                    ss = getattr(item, key_score)
                    bs = getattr(item, key_box, [None] * len(ts))
                    for t, sc, box in zip(ts, ss, bs):
                        texts.append((str(t), float(sc), box))
                    break

        return texts
    
    def _downscale_for_runtime(self, img: Image.Image, max_side: int = 1400) -> Image.Image:
        w, h = img.size
        m = max(w, h)
        if m <= max_side:
            return img
        scale = max_side / float(m)
        nw, nh = int(w * scale), int(h * scale)
        return img.resize((nw, nh), Image.Resampling.LANCZOS)

    def _cap_crop(self, crop: Image.Image, max_w: int, max_h: int) -> Image.Image:
        w, h = crop.size
        if w <= max_w and h <= max_h:
            return crop
        s = min(max_w / float(w), max_h / float(h))
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
        return crop.resize((nw, nh), Image.Resampling.LANCZOS)

    def _best_fraction_from_texts(self, texts: List[Tuple[str, float, object]]) -> Tuple[Optional[Tuple[int,int]], Optional[str], Optional[int]]:
        cleaned = []
        for t, sc, box in texts:
            dt = self._keep_digitish(t)
            if self.DIGITISH_RE.search(dt):
                cleaned.append((dt, sc, box))

        joined = " ".join([t for t, _, _ in cleaned]).strip()

        m = CARD_NO_RE.search(joined)
        if not m:
            return None, None, None

        a = int(m.group(1))
        b = int(m.group(2))
        return (a, b), f"{a}/{b}", a

    def _cap_crop(self, crop: Image.Image, max_w: int, max_h: int) -> Image.Image:
        w, h = crop.size
        if w <= max_w and h <= max_h:
            return crop
        s = min(max_w / float(w), max_h / float(h))
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
        return crop.resize((nw, nh), Image.Resampling.LANCZOS)

    
    def _ocr_set_code(self, img: Image.Image, debug_ocr: Dict) -> Optional[str]:
        """
        Some prints include language suffix, like:
        DRIEN  -> DRI + EN
        We want to return DRI.
        """
        dbg = debug_ocr.setdefault("set_code", {})

        W, H = img.size

        x0 = int(W * 0.02)
        x1 = int(W * 0.22)
        y0 = int(H * 0.86)
        y1 = int(H * 0.985)

        crop = img.crop((x0, y0, x1, y1))

        try:
            top = self.setcode_ocr.predict(crop)
        except Exception as e:
            dbg["error"] = str(e)
            return None

        raw = " ".join([t.get("text", "") for t in (top or [])]).upper()
        raw = re.sub(r"[^A-Z0-9\\s]", " ", raw)
        raw = re.sub(r"\\s+", " ", raw).strip()

        dbg["raw"] = raw

        if not raw:
            return None

        toks = raw.split()

        LANG = {"EN", "FR", "DE", "ES", "IT", "PT", "JP", "KR", "CN"} # different lang symbols

        for tok in toks:
            tok = re.sub(r"[^A-Z0-9]", "", tok)

            if 2 <= len(tok) <= 4:
                dbg["picked"] = tok
                return tok

            if len(tok) >= 5:
                prefix3 = tok[:3]
                suffix2 = tok[3:5]
                if prefix3.isalnum() and suffix2 in LANG:
                    dbg["picked"] = prefix3
                    return prefix3

                prefix4 = tok[:4]
                suffix2b = tok[4:6]
                if prefix4.isalnum() and suffix2b in LANG:
                    dbg["picked"] = prefix4
                    return prefix4

        return None

    def _api_search_by_set_code_number(
        self,
        *,
        set_code: str,
        number_only: Optional[int],
        frac: Optional[Tuple[int, int]],
    ) -> List[Dict]:
        if not set_code or number_only is None:
            return []

        attempted = []
        last_error = None
        if frac is not None:
            a, b = frac
            q = f'set.ptcgoCode:{set_code} number:{a} set.printedTotal:{b}'
            attempted.append(q)
            try:
                res = self.api_client.search_cards(q=q, page_size=40)
                if res:
                    self._last_api_debug = {
                        "query_used": q,
                        "attempted": attempted,
                        "last_error": None,
                        "used_set_code": True,
                        "used_fraction": True,
                    }
                    return res
            except Exception as e:
                last_error = str(e)

        q = f'set.ptcgoCode:{set_code} number:{int(number_only)}'
        attempted.append(q)
        try:
            res = self.api_client.search_cards(q=q, page_size=40)
            self._last_api_debug = {
                "query_used": q if res else None,
                "attempted": attempted,
                "last_error": last_error,
                "used_set_code": True,
                "used_fraction": False,
            }
            return res or []
        except Exception as e:
            self._last_api_debug = {
                "query_used": None,
                "attempted": attempted,
                "last_error": str(e),
                "used_set_code": True,
                "used_fraction": False,
            }
            return []

    def _prep_ocr_crop(self, crop: Image.Image) -> np.ndarray:
        gray = np.array(crop.convert("L"))
        gray = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        gray = cv2.bilateralFilter(gray, d=7, sigmaColor=40, sigmaSpace=40)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
        sharp = cv2.addWeighted(gray, 1.6, blur, -0.6, 0)

        _, th = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        return th
    
    def _prep_title_crop(self, crop: Image.Image) -> np.ndarray:
        """
        Stronger title preprocessing (safe, still fast):
        - upscale more 
        - contrast boost
        - mild sharpen
        - NO hard threshold 
        """
        crop = ImageOps.autocontrast(crop)
        gray = np.array(crop.convert("L"))

        gray = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)

        gray = cv2.bilateralFilter(gray, d=7, sigmaColor=40, sigmaSpace=40)
        clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
        sharp = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)

        return sharp

    def _ocr_text(self, crop: Image.Image, mode: str) -> Tuple[str, Dict]:
        dbg: Dict = {"engine": None, "raw": None}
        if mode == "setcode":
            img_np = self._prep_title_crop(crop)

            if easyocr is not None and self._get_easyocr() is not None:
                try:
                    reader = self._get_easyocr()
                    res = reader.readtext(
                        img_np,
                        detail=1,
                        paragraph=False,
                        allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                    )

                    best_code = None
                    best_conf = -1.0
                    best_raw = None

                    for item in res:
                        if not item or len(item) < 3:
                            continue
                        txt = (item[1] or "").upper().strip()
                        conf = float(item[2] or 0.0)

                        txt2 = txt.replace("|", "").replace(" ", "")
                        if not re.fullmatch(r"[A-Z0-9]{2,4}", txt2):
                            continue

                        if conf > best_conf:
                            best_conf = conf
                            best_code = txt2
                            best_raw = txt

                    dbg["engine"] = "easyocr"
                    dbg["raw"] = best_raw
                    dbg["best_code"] = best_code
                    dbg["best_conf"] = best_conf

                    if best_code and best_conf >= 0.80:
                        return best_code, dbg
                    return "", dbg

                except Exception as e:
                    dbg["easyocr_error"] = str(e)

            if pytesseract is not None:
                try:
                    cfg = r"--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                    raw = (pytesseract.image_to_string(img_np, config=cfg) or "").upper().strip()
                    raw = raw.replace(" ", "").replace("|", "")

                    dbg["engine"] = "tesseract"
                    dbg["raw"] = raw

                    m = re.search(r"\b([A-Z0-9]{2,4})\b", raw)
                    if m:
                        return m.group(1), dbg
                    return "", dbg
                except Exception as e:
                    dbg["tesseract_error"] = str(e)

            return "", dbg

        if mode == "title":
            img_np = self._prep_title_crop(crop)

            if easyocr is not None and self._get_easyocr() is not None:
                try:
                    reader = self._get_easyocr()
                    res = reader.readtext(img_np, detail=0, paragraph=False)
                    text = " ".join(r.strip() for r in res if r and r.strip()).strip()
                    dbg["engine"] = "easyocr"
                    dbg["raw"] = text
                    return text, dbg
                except Exception as e:
                    dbg["easyocr_error"] = str(e)

            if pytesseract is not None:
                try:
                    cfg = r"--oem 3 --psm 7"
                    text = (pytesseract.image_to_string(img_np, config=cfg) or "").strip()
                    dbg["engine"] = "tesseract"
                    dbg["raw"] = text
                    return text, dbg
                except Exception as e:
                    dbg["tesseract_error"] = str(e)

            return "", dbg

        if mode == "number":
            img_np = self._prep_ocr_crop(crop)

            def score_fraction_text(s: str) -> int:
                if not s:
                    return -999
                s = s.strip()
                digits = sum(ch.isdigit() for ch in s)
                slashes = s.count("/")
                letters = sum(ch.isalpha() for ch in s)
                penalty = s.count("\n") + s.count(" ") + letters
                score = digits + (20 * slashes) - (2 * penalty)
                if CARD_NO_RE.search(s):
                    score += 30
                return score

            best_text = ""
            best_score = -10**9
            best_meta: Dict = {}

            if easyocr is not None and self._get_easyocr() is not None:
                try:
                    reader = self._get_easyocr()
                    candidates = [
                        ("easyocr_normal", img_np),
                        ("easyocr_inverted", 255 - img_np),
                    ]

                    for variant, cand in candidates:
                        res = reader.readtext(
                            cand,
                            detail=0,
                            paragraph=False,
                            allowlist="0123456789/",
                        )
                        text = " ".join(r.strip() for r in res if r and r.strip()).strip()
                        sc = score_fraction_text(text)
                        if sc > best_score:
                            best_score = sc
                            best_text = text
                            best_meta = {"engine": "easyocr", "variant": variant}
                except Exception as e:
                    dbg["easyocr_error"] = str(e)

            if (best_score < 20) and pytesseract is not None:
                try:
                    whitelist = r"-c tessedit_char_whitelist=0123456789/"
                    psms = [7, 8, 6, 11, 13]

                    for variant, cand in [
                        ("tess_normal", img_np),
                        ("tess_inverted", 255 - img_np),
                    ]:
                        for psm in psms:
                            cfg = f"--oem 3 --psm {psm} {whitelist}"
                            text = (pytesseract.image_to_string(cand, config=cfg) or "").strip()
                            sc = score_fraction_text(text)
                            if sc > best_score:
                                best_score = sc
                                best_text = text
                                best_meta = {"engine": "tesseract", "variant": variant, "psm": psm}
                except Exception as e:
                    dbg["tesseract_error"] = str(e)

            dbg["engine"] = best_meta.get("engine")
            dbg["raw"] = best_text
            dbg["score"] = best_score
            dbg.update(best_meta)
            return best_text, dbg

        dbg["error"] = f"unknown mode: {mode}"
        return "", dbg

    def _get_easyocr(self):
        if easyocr is None:
            return None
        if self._easy_reader is None:
            # EasyOCR GPU=True only works if torch CUDA is available.
            # On Mac (MPS), EasyOCR typically still runs CPU.
            gpu_flag = False
            try:
                import torch
                gpu_flag = bool(torch.cuda.is_available())
            except Exception:
                gpu_flag = False

            if os.environ.get("EASYOCR_GPU", "").strip() in ("1", "true", "True"):
                gpu_flag = True

            self._easy_reader = easyocr.Reader(["en"], gpu=gpu_flag)
        return self._easy_reader


    def _normalize_name(self, s: str) -> Optional[str]:
        s = (s or "").strip()
        s = s.replace("_", " ")
        s = re.sub(r"[^A-Za-z \-']", " ", s)
        s = re.sub(r"\s+", " ", s).strip()

        if len(s) < 3:
            return None
        return s

    def _clean_name_token(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None

        name = name.replace("’", "'").strip()
        if len(name) < 2:
            return None

        clean = re.sub(r"[^A-Za-z0-9 '\-]", " ", name)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return None

        toks = clean.split()

        if len(toks) >= 2:
            first = toks[0].lower()
            second = toks[1].lower()

            if first.endswith("'s") or (first.isalpha() and second in {"'s", "s"}):
                # drop first token(s) that represent the trainer name + possessive
                if second in {"'s", "s"} and len(toks) >= 3:
                    toks = toks[2:]
                else:
                    toks = toks[1:]

        if not toks:
            return None

        if len(toks) >= 2:
            return " ".join(toks[:2])
        return toks[0]


    def _parse_fraction(self, s: str) -> Optional[Tuple[int, int]]:
        if not s:
            return None
        m = CARD_NO_RE.search(s)
        if not m:
            return None
        try:
            return int(m.group(1)), int(m.group(2))
        except Exception:
            return None

    def _fix_smashed_fraction(self, s: str) -> Optional[Tuple[int, int]]:
        if not s:
            return None

        t = "".join(ch for ch in s if ch.isdigit() or ch == "/")
        m = CARD_NO_RE.search(t)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 1 <= a <= 999 and 10 <= b <= 9999:
                return (a, b)

        digits = "".join(ch for ch in t if ch.isdigit())
        if len(digits) < 4:
            return None

        candidates: List[Tuple[int, int]] = []
        for trim in range(0, 4):
            dd = digits[:-trim] if trim > 0 else digits
            if len(dd) < 4:
                continue

            for k in (2, 3):
                if len(dd) <= k:
                    continue
                a = int(dd[:k])
                b = int(dd[k:])
                if not (1 <= a <= 999 and 10 <= b <= 9999):
                    continue

                if b > 600:
                    continue
                if a > b:
                    continue

                candidates.append((a, b))

        if not candidates:
            return None

        def score(x: Tuple[int, int]) -> int:
            a, b = x
            s = 0
            if 50 <= b <= 300:
                s += 10
            if 10 <= a <= 200:
                s += 3
            s -= abs(b - 150) // 10
            return s

        candidates.sort(key=score, reverse=True)
        return candidates[0]

    def _ocr_title_yolo(self, img: Image.Image, dbg: Dict) -> Optional[str]:
        def _title_quality(s: str) -> int:
            s = (s or "").strip()
            if not s:
                return -999

            cleaned = re.sub(r"[^A-Za-z \-']", " ", s)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()

            if len(cleaned) < 3:
                return -999

            letters = sum(ch.isalpha() for ch in cleaned)
            spaces = cleaned.count(" ")
            bad_words = {"pokemon", "trainer", "energy", "stage", "evolves", "from"}

            toks = [t for t in cleaned.split() if t]
            bad = sum(1 for t in toks if t.lower() in bad_words)

            score = 0
            score += letters
            score += 8 if 1 <= len(toks) <= 2 else 0
            score -= bad * 8
            score -= max(0, spaces - 2) * 2
            return score

        candidates: List[Tuple[str, Image.Image, Dict]] = []
        if self.yolo_detector is not None:
            try:
                dets = self.yolo_detector.detect(img)
            except Exception as e:
                dbg["title_yolo_detect_error"] = str(e)
                dets = {}

            if "title" in dets:
                crop = self.yolo_detector.crop(img, dets["title"], pad=12)
                crop = self._cap_crop(crop, max_w=1400, max_h=340)
                candidates.append(("yolo", crop, {"title_yolo_det": dets["title"].__dict__}))
            else:
                dbg["title_yolo_missing"] = True

        w, h = img.size
        fallback_boxes = [
            # classic title band
            ("fb_top", (int(w * 0.10), int(h * 0.05), int(w * 0.90), int(h * 0.23))),
            # a bit lower
            ("fb_mid", (int(w * 0.10), int(h * 0.10), int(w * 0.90), int(h * 0.30))),
            # lower band (some promos / special layouts)
            ("fb_lower", (int(w * 0.10), int(h * 0.18), int(w * 0.90), int(h * 0.38))),
        ]

        for tag, box in fallback_boxes:
            crop = img.crop(box)
            crop = self._cap_crop(crop, max_w=1400, max_h=360)
            candidates.append((tag, crop, {"title_fallback_box": box}))

        best_text = None
        best_dbg = None
        best_score = -10**9

        for tag, crop, meta in candidates:
            crop2 = ImageOps.autocontrast(crop)

            text, odbg = self._ocr_text(crop2, mode="title")
            norm = self._normalize_name(text)

            cand_dbg = {**meta, "title_source": tag, "title_ocr": odbg, "crop_size": [crop2.size[1], crop2.size[0]], "raw_text": text, "norm_text": norm}
            q = _title_quality(norm or "")
            cand_dbg["title_quality"] = q
            dbg.setdefault("title_candidates", [])
            dbg["title_candidates"].append(cand_dbg)

            if q > best_score:
                best_score = q
                best_text = norm
                best_dbg = cand_dbg

        dbg["title_best"] = best_dbg
        return best_text

    def _trim_printed_total_if_needed(self, a: int, b: int) -> Optional[Tuple[int, int]]:
        b_str = str(b)
        if len(b_str) == 4:
            b_fixed = int(b_str[:-1])
            if 10 <= b_fixed <= 400:
                return (a, b_fixed)
        return None
    def _fraction_plausibility(self, a: int, b: int) -> int:
        score = 0

        if b <= 0 or a <= 0:
            return -999

        if 20 <= b <= 400:
            score += 8
        elif 10 <= b <= 600:
            score += 4
        else:
            score -= 8  # very large totals are often OCR soup

        if 1 <= a <= 400:
            score += 3
        else:
            score -= 6

        # a <= b is common but NOT guaranteed; treat as a bonus only
        if a <= b:
            score += 3
        else:
            score -= 1

        # If b is very small (< 10), it's almost never a printed total
        if b < 10:
            score -= 10

        return score
    
    def _ocr_number_yolo(
        self, img: Image.Image, dbg: Dict
    ) -> Tuple[Optional[Tuple[int, int]], Optional[str], Optional[int], str, int]:
        ocr = self._get_paddleocr()
        if ocr is None:
            dbg["paddle_missing"] = True
            return None, None, None, "none", -999

        def _prep_number_crop(c: Image.Image) -> Image.Image:
            c = self._cap_crop(c, max_w=820, max_h=380)

            g = c.convert("L")
            g = ImageOps.autocontrast(g)
            g = g.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

            w, h = g.size
            if w < 520 and h < 260:
                g = g.resize((w * 2, h * 2), Image.Resampling.LANCZOS)

            return g.convert("RGB")

        def run_paddle(crop_img: Image.Image, cand_dbg: Dict) -> Tuple[List[Tuple[str, float, Any]], str]:
            bgr = self._pil_to_bgr(crop_img)
            try:
                raw = ocr.predict(bgr)
                used = "predict"
                texts = self._parse_new_paddleocr_result(raw)
            except Exception as e1:
                cand_dbg["predict_error"] = str(e1)
                raw = ocr.ocr(bgr)
                used = "ocr"
                texts = self._parse_old_paddleocr_result(raw)

            cand_dbg["paddle_used"] = used
            return texts or [], used

        def digitish_join(texts: List[Tuple[str, float, Any]]) -> str:
            toks = []
            for t, _, _ in texts:
                if not t:
                    continue
                s = "".join(ch for ch in str(t) if ch.isdigit() or ch == "/")
                s = re.sub(r"/{2,}", "/", s).strip()
                if s:
                    toks.append(s)
            return " ".join(toks).strip()

        def score_candidate(plaus: int, top_score: float, frac_tuple: Optional[Tuple[int, int]], source: str) -> float:
            score = float(plaus) * 10.0 + float(top_score) * 2.0
            if source == "yolo":
                score += 3.0
            if frac_tuple is not None:
                a, b = frac_tuple
                if b < 30:
                    score -= 60.0
                if b > 450:
                    score -= 40.0
                if a <= 0 or b <= 0:
                    score -= 80.0
                if a > 500:
                    score -= 30.0
                if b >= 200 and a <= 5:
                    score -= 25.0
            return score

        def _extract_best_fraction_from_joined(joined: str) -> Optional[Tuple[int, int, str, float]]:
            if not joined:
                return None

            matches = list(re.finditer(r"(\d{1,4})\s*/\s*(\d{1,4})", joined))
            if not matches:
                return None

            best = None
            best_s = -1e9

            for m in matches:
                try:
                    a = int(m.group(1))
                    b = int(m.group(2))
                except Exception:
                    continue

                if a <= 0 or b <= 0:
                    continue
                if b < 30 or b > 450:
                    base_pen = -50.0
                else:
                    base_pen = 0.0

                plaus = int(self._fraction_plausibility(a, b))


                pos = m.start() / max(1, len(joined))
                late_bonus = pos * 6.0  # 0..6

                len_bonus = 0.0
                if 1 <= len(m.group(1)) <= 3 and 2 <= len(m.group(2)) <= 3:
                    len_bonus += 1.5

                s = plaus * 10.0 + late_bonus + len_bonus + base_pen

                if s > best_s:
                    best_s = s
                    best = (a, b, f"{a}/{b}", float(best_s))

            return best

        candidates: List[Tuple[str, str, Image.Image, Dict]] = []

        dets = None
        if self.yolo_detector is not None:
            try:
                dets = self.yolo_detector.detect(img)
            except Exception as e:
                dbg["number_yolo_detect_error"] = str(e)
                dets = None
        else:
            dbg["number_yolo_disabled"] = True

        if dets and "card_number" in dets:
            dbg["number_yolo_det"] = dets["card_number"].__dict__
            for pad in (8, 14, 22):
                try:
                    crop = self.yolo_detector.crop(img, dets["card_number"], pad=pad)
                    crop = self._cap_crop(crop, max_w=1000, max_h=480)
                    cand_dbg = {"pad": pad, "crop_shape": [crop.size[1], crop.size[0]]}
                    candidates.append(("yolo", f"yolo_pad{pad}", crop, cand_dbg))
                except Exception as e:
                    dbg[f"number_yolo_crop_error_pad{pad}"] = str(e)
        else:
            dbg["number_yolo_missing"] = True

        w, h = img.size
        fallback_boxes = [
            # bottom-right (classic)
            (int(w * 0.72), int(h * 0.84), int(w * 0.99), int(h * 0.98)),
            (int(w * 0.55), int(h * 0.84), int(w * 0.99), int(h * 0.99)),
            (int(w * 0.02), int(h * 0.84), int(w * 0.35), int(h * 0.98)),  # wider
            (int(w * 0.00), int(h * 0.84), int(w * 0.42), int(h * 0.99)),  # taller, helps when sleeve glare pushes text up
            # bottom-center (some promos / odd layouts)
            (int(w * 0.35), int(h * 0.84), int(w * 0.70), int(h * 0.99)),
        ]

        for i, box in enumerate(fallback_boxes):
            crop = img.crop(box)
            crop = self._cap_crop(crop, max_w=900, max_h=420)
            cand_dbg = {"fallback_box": box, "crop_shape": [crop.size[1], crop.size[0]]}
            candidates.append(("fallback", f"fallback_{i}", crop, cand_dbg))

        if not candidates:
            dbg["number_no_candidates"] = True
            return None, None, None, "none", -999

        best = None

        for source, tag, crop, cand_dbg in candidates:
            dbg_key = f"number_{tag}"
            dbg[dbg_key] = cand_dbg

            cw, ch = crop.size
            dbg[dbg_key]["crop_size_before_prep"] = [ch, cw]
            if cw * ch > 900 * 500:
                dbg[dbg_key]["skipped_reason"] = "crop_too_large_after_cap"
                continue

            pre = _prep_number_crop(crop)
            dbg[dbg_key]["prep_size_after"] = [pre.size[1], pre.size[0]]

            texts, used = run_paddle(pre, dbg[dbg_key])
            if not texts:
                dbg[dbg_key]["paddle_no_text"] = True
                continue

            texts.sort(key=lambda t: t[1], reverse=True)
            dbg[dbg_key]["paddle_top"] = [{"text": t, "score": float(sc)} for (t, sc, _) in texts[:25]]

            joined = digitish_join(texts)
            dbg[dbg_key]["digitish_joined"] = joined

            frac_tuple, frac_raw, number_only = self._best_fraction_from_texts(texts)

            extracted = _extract_best_fraction_from_joined(joined)
            if extracted is not None:
                a2, b2, raw2, bonus2 = extracted
                dbg[dbg_key]["joined_fraction_pick"] = {"a": a2, "b": b2, "raw": raw2, "bonus": bonus2}
                if frac_tuple is None:
                    frac_tuple, frac_raw, number_only = (a2, b2), raw2, a2
                else:
                    a1, b1 = frac_tuple
                    p1 = int(self._fraction_plausibility(a1, b1))
                    p2 = int(self._fraction_plausibility(a2, b2))
                    if p2 >= p1 + 3:
                        frac_tuple, frac_raw, number_only = (a2, b2), raw2, a2

            if frac_tuple is None:
                dbg[dbg_key]["digitish_join_failed"] = True
                continue

            a, b = frac_tuple
            plaus = int(self._fraction_plausibility(a, b))
            dbg[dbg_key]["fraction_plausibility"] = {"a": a, "b": b, "score": plaus}
            dbg[dbg_key]["fraction_raw"] = frac_raw

            top_score = float(texts[0][1]) if texts else 0.0
            overall = score_candidate(plaus, top_score, frac_tuple, source)
            dbg[dbg_key]["candidate_score"] = overall

            if plaus >= 14 and top_score >= 0.97:
                best = (overall, plaus, frac_tuple, frac_raw, number_only, source, dbg_key)
                break

            if best is None or overall > best[0]:
                best = (overall, plaus, frac_tuple, frac_raw, number_only, source, dbg_key)

        if best is None:
            dbg["number_all_candidates_failed"] = True
            return None, None, None, "fallback", -999

        _, plaus, frac_tuple, frac_raw, number_only, source, dbg_key = best

        if source == "yolo":
            dbg["number_yolo"] = dbg.get(dbg_key, {})
        else:
            dbg["number_fallback"] = dbg.get(dbg_key, {})

        return frac_tuple, frac_raw, number_only, source, plaus

    
    def _extract_tcgplayer_id(self, card_obj: Dict) -> Optional[int]:
        if not isinstance(card_obj, dict):
            return None

        tcg = card_obj.get("tcgplayer") or {}
        if not isinstance(tcg, dict):
            return None

        for k in ("productId", "productID", "tcgplayerId", "tcgplayer_id", "id"):
            v = tcg.get(k)
            if v is None:
                continue
            try:
                iv = int(v)
                if iv > 0:
                    return iv
            except Exception:
                pass

        url = tcg.get("url")
        if isinstance(url, str) and url:
            import re
            nums = re.findall(r"(\d{5,9})", url)
            if nums:
                nums.sort(key=len, reverse=True)
                try:
                    return int(nums[0])
                except Exception:
                    return None

        return None
    
    # API candidate search
    def _api_search_candidates(
        self,
        frac: Optional[Tuple[int, int]],
        number_only: Optional[int],
        name_token: Optional[str],
    ) -> List[Dict]:
        attempted: List[str] = []
        last_err: Optional[str] = None

        def _quote_name(s: str) -> str:
            s = (s or "").replace("’", "'")
            s = re.sub(r"[^A-Za-z0-9 \-']", " ", s)
            s = re.sub(r"\s+", " ", s).strip()
            if not s:
                return ""
            return f'"{s}"' if " " in s else s

        def _name_match_score(card: Dict, token: str) -> int:
            """
            Score how well a card's name matches the OCR token.
            Used to break ties when many cards share the same fraction.
            """
            if not token:
                return 0
            nm = (card.get("name") or "").lower()
            tok = token.lower().strip()

            score = 0
            if tok in nm:
                score += 50

            for w in tok.split():
                if w in nm:
                    score += 10

            return score

        # 1) BEST: number + printedTotal
        if frac is not None:
            a, b = frac
            q = f"number:{a} set.printedTotal:{b}"
            attempted.append(q)

            try:
                cards = self.api_client.search_cards(q=q, page_size=self.max_candidates)
                if cards:
                    if name_token:
                        scored = [( _name_match_score(c, name_token), c) for c in cards]
                        filtered = [c for s, c in scored if s > 0]

                        if filtered:
                            cards = filtered
                        else:
                            scored.sort(key=lambda x: x[0], reverse=True)
                            cards = [c for _, c in scored]

                    self._last_api_debug = {
                        "query_used": q,
                        "attempted": attempted,
                        "last_error": None,
                        "name_trusted": bool(name_token),
                        "used_fraction": True,
                        "fraction_filtered_by_name": bool(name_token),
                        "returned_count": len(cards),
                    }
                    return cards

            except Exception as e:
                last_err = str(e)

        # 2) If we have a name token, do name-only
        nm = _quote_name(name_token) if name_token else ""
        if nm:
            q1 = f"name:{nm}"
            attempted.append(q1)
            try:
                cards = self.api_client.search_cards(q=q1, page_size=self.max_candidates)
                if cards:
                    if number_only is not None and number_only >= 10:
                        qn = f"number:{int(number_only)} name:{nm}"
                        attempted.append(qn)
                        try:
                            narrowed = self.api_client.search_cards(q=qn, page_size=self.max_candidates)
                            if narrowed:
                                self._last_api_debug = {
                                    "query_used": qn,
                                    "attempted": attempted,
                                    "last_error": None,
                                    "name_trusted": True,
                                    "used_fraction": False,
                                    "returned_count": len(narrowed),
                                }
                                return narrowed
                        except Exception as e:
                            last_err = str(e)

                    self._last_api_debug = {
                        "query_used": q1,
                        "attempted": attempted,
                        "last_error": None,
                        "name_trusted": True,
                        "used_fraction": False,
                        "returned_count": len(cards),
                    }
                    return cards
            except Exception as e:
                last_err = str(e)

        # 3) Fallback: number-only query if nothing else worked
        if number_only is not None:
            qn = f"number:{int(number_only)}"
            attempted.append(qn)
            try:
                cards = self.api_client.search_cards(q=qn, page_size=self.max_candidates)
                if cards:
                    self._last_api_debug = {
                        "query_used": qn,
                        "attempted": attempted,
                        "last_error": None,
                        "name_trusted": False,
                        "used_fraction": False,
                        "returned_count": len(cards),
                    }
                    return cards
            except Exception as e:
                last_err = str(e)

        self._last_api_debug = {
            "query_used": None,
            "attempted": attempted,
            "last_error": last_err,
            "name_trusted": bool(name_token),
            "used_fraction": bool(frac),
            "returned_count": 0,
        }
        return []

    # Visual verify
    def _safe_int(self, x) -> Optional[int]:
        try:
            if x is None:
                return None
            s = str(x).strip()
            s = re.sub(r"[^0-9]", "", s)
            if not s:
                return None
            return int(s)
        except Exception:
            return None

    def _candidate_number_tuple(self, c: Dict) -> Tuple[Optional[int], Optional[int]]:
        a = self._safe_int(c.get("number"))
        b = None
        st = c.get("set") or {}
        b = self._safe_int(st.get("printedTotal"))
        return a, b

    def _number_hint_score(
        self,
        hint_a: Optional[int],
        hint_b: Optional[int],
        cand_a: Optional[int],
        cand_b: Optional[int],
    ) -> float:
        if hint_a is None and hint_b is None:
            return 0.0

        score = 0.0

        if hint_a is not None and cand_a is not None:
            if hint_a == cand_a:
                score += 2.0
            elif abs(hint_a - cand_a) <= 1:
                score += 0.6  # tiny OCR slip
            else:
                if hint_a >= 10 and cand_a >= 10 and (hint_a % 100) == (cand_a % 100):
                    score += 0.3

        if hint_b is not None and cand_b is not None:
            if hint_b == cand_b:
                score += 1.0
            elif abs(hint_b - cand_b) <= 1:
                score += 0.3

        return score
    
    def _find_reference_image(self, card_id: str) -> Optional[Path]:
        if not card_id:
            return None

        base = Path(self.reference_images_dir) if self.reference_images_dir else None
        if not base or not base.exists():
            return None

        exts = [".png", ".jpg", ".jpeg", ".webp"]

        # 1) direct hit
        for ext in exts:
            p = base / f"{card_id}{ext}"
            if p.exists():
                return p

        # 2) common subfolder
        for ext in exts:
            p = base / "images" / f"{card_id}{ext}"
            if p.exists():
                return p
        # 3) fallback recursive
        try:
            for ext in exts:
                hits = list(base.rglob(f"{card_id}{ext}"))
                if hits:
                    return hits[0]
        except Exception:
            return None

        return None

    def _get_candidate_image(self, card_id: str, url: str) -> Path:
        out = self.cache_ref_dir / f"{card_id}.png"
        if out.exists():
            return out

        last_err = None
        for i in range(self.download_retries):
            try:
                r = requests.get(url, timeout=self.download_timeout)
                r.raise_for_status()
                out.write_bytes(r.content)
                return out
            except Exception as e:
                last_err = e
                time.sleep(0.8 * (i + 1))

        raise RuntimeError(f"Failed downloading candidate image for {card_id}: {last_err}")

    def _verify_candidates_visually(
        self, user_img: Image.Image, candidates: List[Dict]
    ) -> List[Tuple[str, float, Dict]]:
        user_vec = self.embedder.embed(user_img)
        scored: List[Tuple[str, float, Dict]] = []

        for c in candidates[: self.visual_top_k]:
            cid = c.get("id")
            if not cid:
                continue

            images = c.get("images") or {}
            url = images.get("large") or images.get("small")

            try:
                ref_path = self._find_reference_image(cid)
                if ref_path:
                    fp = ref_path
                else:
                    if not url:
                        continue
                    fp = self._get_candidate_image(cid, url)

                cand_img = Image.open(fp).convert("RGB")
                cand_vec = self.embedder.embed(cand_img)

                sim = float(np.dot(user_vec, cand_vec))
                scored.append((cid, sim, c))
            except Exception:
                continue

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # Main
    def identify(
        self,
        image_path: str,
        progress_cb: Optional[Callable[[str, str], None]] = None,
    ) -> IdentificationResult:
        img = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
        img = self._downscale_for_runtime(img, max_side=1400)
        debug: Dict = {"ocr": {}, "api": {}, "visual": {}, "pricing": {}}
        
        def _progress(stage: str, detail: str = "") -> None:
            if not progress_cb:
                return
            try:
                progress_cb(stage, detail)
            except Exception:
                # Never allow UI callbacks to break identification
                pass

        _progress("Loading image", "Preparing scan…")

        # PriceCharting helper
        def _maybe_attach_pricecharting(
            card_name: Optional[str],
            set_name: Optional[str],
            card_number: Optional[str],
        ) -> None:
            try:
                from pokemon_valuator.integrations.pricecharting_public import (
                    search_pricecharting_pages,
                    scrape_pricecharting_psa_prices,
                )
            except Exception as e:
                debug["pricecharting"] = {"status": "import_error", "error": str(e)}
                return

            if not card_name or not set_name or not card_number:
                debug["pricecharting"] = {
                    "status": "missing_fields",
                    "card_name": card_name,
                    "set_name": set_name,
                    "card_number": card_number,
                }
                return

            import re
            from urllib.parse import urlparse, unquote

            def slugify(s: str) -> str:
                s = (s or "").lower().strip()
                s = s.replace("&", "and")
                s = s.replace("’", "'")
                s = s.replace("'", "")
                s = re.sub(r"[^a-z0-9]+", "-", s)
                s = re.sub(r"-{2,}", "-", s).strip("-")
                return s

            def normalize_path(url: str) -> str:
                # unquote turns %27 into '
                path = unquote(urlparse(url).path).lower()
                path = path.replace("’", "'").replace("'", "")
                path = re.sub(r"[^a-z0-9/\-]+", "", path)
                return path
            number_only_str = None
            try:
                number_only_str = str(int(str(card_number).split("/")[0].strip()))
            except Exception:
                number_only_str = None

            set_slug = slugify(set_name)              # "destined-rivals"
            card_slug = slugify(card_name)            # "arvens-toedscool"
            required_set_fragment = f"/game/pokemon-{set_slug}/"

            try:
                resolved_list = search_pricecharting_pages(
                    card_name=card_name,
                    set_name=set_name,
                    card_number=card_number,
                    limit=12,  # allow extra, we filter hard below
                )
            except Exception as e:
                debug["pricecharting"] = {"status": "search_error", "error": str(e)}
                return

            if not resolved_list:
                debug["pricecharting"] = {
                    "status": "not_found",
                    "card_name": card_name,
                    "set_name": set_name,
                    "card_number": card_number,
                }
                return
            filtered = []
            dropped = []
            for r in resolved_list:
                url = getattr(r, "url", None)
                title = getattr(r, "title", None)
                if not url:
                    continue

                path = normalize_path(url)

                ok_set = required_set_fragment in path
                ok_name = (f"/{card_slug}" in path)
                ok_num = True
                if number_only_str:
                    ok_num = path.endswith(f"-{number_only_str}")

                if ok_set and ok_name and ok_num:
                    filtered.append(r)
                else:
                    dropped.append(
                        {
                            "url": url,
                            "title": title,
                            "reasons": {
                                "set_ok": ok_set,
                                "name_ok": ok_name,
                                "num_ok": ok_num,
                                "path": path,
                            },
                        }
                    )

            if filtered:
                resolved_list = filtered
                filter_status = "filtered"
            else:
                filter_status = "filter_empty_fallback_unfiltered"

            debug["pricecharting"] = {
                "status": "resolved_multi",
                "filter_status": filter_status,
                "query": {
                    "card_name": card_name,
                    "set_name": set_name,
                    "card_number": card_number,
                    "card_slug": card_slug,
                    "set_slug": set_slug,
                    "number_only": number_only_str,
                },
                "dropped": dropped[:20],
                "candidates": [
                    {"url": getattr(r, "url", None), "title": getattr(r, "title", None)}
                    for r in resolved_list
                    if getattr(r, "url", None)
                ],
            }

            variants = []
            for r in resolved_list:
                url = getattr(r, "url", None)
                if not url:
                    continue
                try:
                    prices = scrape_pricecharting_psa_prices(url=url)
                except Exception as e:
                    prices = {"status": "scrape_error", "error": str(e), "url": url}

                variants.append(
                    {
                        "url": url,
                        "title": getattr(r, "title", None),
                        "prices": prices,
                    }
                )

            debug["pricecharting"]["variants"] = variants

        def _tcgdex_force_usd(detail: Dict, eur_to_usd: float = 1.10) -> Dict:
            if not isinstance(detail, dict):
                return detail

            pricing = detail.get("pricing") or {}

            tcgplayer = pricing.get("tcgplayer")
            cardmarket = pricing.get("cardmarket")

            # If tcgplayer exists, prefer it (USD)
            if tcgplayer:
                pricing["preferred"] = {"source": "tcgplayer", "unit": "USD", "data": tcgplayer}
                return detail

            # Otherwise convert cardmarket EUR -> USD
            if cardmarket and (cardmarket.get("unit") == "EUR"):
                converted = {}
                for k, v in cardmarket.items():
                    if isinstance(v, (int, float)):
                        converted[k] = round(float(v) * eur_to_usd, 4)
                    else:
                        converted[k] = v

                converted["unit"] = "USD"
                converted["converted_from"] = "EUR"
                converted["eur_to_usd_rate"] = eur_to_usd

                pricing["cardmarket_usd"] = converted
                pricing["preferred"] = {"source": "cardmarket_converted", "unit": "USD", "data": converted}

            detail["pricing"] = pricing
            return detail
        def _stash_pokemontcg_prices(card_obj: Dict) -> None:
            """
            Put PokemonTCG-provided price blocks somewhere stable.
            This includes tcgplayer low/mid/high/market/directLow/etc, and cardmarket prices.
            """
            tcg_block = card_obj.get("tcgplayer") or {}
            cm_block = card_obj.get("cardmarket") or {}

            debug["pricing"].setdefault("pokemontcg", {})
            debug["pricing"]["pokemontcg"]["tcgplayer_url"] = tcg_block.get("url")
            debug["pricing"]["pokemontcg"]["tcgplayer_prices"] = (tcg_block.get("prices") or {})
            debug["pricing"]["pokemontcg"]["cardmarket_prices"] = (cm_block.get("prices") or {})

            debug["api"]["tcgplayer_block_present"] = bool(tcg_block)
            debug["api"]["cardmarket_block_present"] = bool(cm_block)
            debug["api"]["tcgplayer_prices"] = (tcg_block.get("prices") or {})
            debug["api"]["cardmarket_prices"] = (cm_block.get("prices") or {})

        def _fetch_full_card_if_possible(card_obj: Dict) -> Dict:
            cid = (card_obj or {}).get("id")
            if not cid:
                return card_obj
            try:
                full = self.api_client.get_card_by_id(cid)
                if full:
                    debug["api"]["fetched_full_card_for_tcgplayer_id"] = True
                    return full
            except Exception as e:
                debug["api"]["full_card_fetch_error"] = str(e)
            return card_obj

        def _attach_pricing_for_final_card(final_card: Dict) -> None:
            """
            Always stash PokemonTCG prices and attempt PriceCharting + TCGdex.
            Uses final_card fields (NOT the shallow search result).
            """
            if not final_card:
                return

            _progress("Fetching prices", "Collecting market prices…")
            _stash_pokemontcg_prices(final_card)

            _progress("Fetching variants", "Resolving PriceCharting variants…")
            _maybe_attach_pricecharting(
                card_name=final_card.get("name"),
                set_name=(final_card.get("set") or {}).get("name"),
                card_number=card_number_str,
            )

            # 3) TCGdex
            _progress("Fetching prices", "Pulling TCGdex pricing…")

            try:
                from pokemon_valuator.integrations.tcgdex_public import search_cards, get_card_detail

                nm = (final_card or {}).get("name")
                if not nm or not number_only:
                    debug["pricing"]["tcgdex"] = {
                        "status": "missing_fields",
                        "name": nm,
                        "localId": number_only,
                    }
                else:
                    hits = search_cards(name=str(nm), local_id=str(number_only), timeout_sec=8)

                    if not hits:
                        debug["pricing"]["tcgdex"] = {
                            "status": "not_found",
                            "query": {"name": nm, "localId": str(number_only)},
                        }
                    else:
                        target_set = ((final_card.get("set") or {}).get("name") or "").strip().lower()
                        chosen = hits[0]

                        if len(hits) > 1 and target_set:
                            for h in hits[:3]:
                                try:
                                    d = get_card_detail(h.id, timeout_sec=8)
                                    setname = ((d.get("set") or {}).get("name") or "").strip().lower()
                                    if setname and setname == target_set:
                                        chosen = h
                                        break
                                except Exception:
                                    continue
                        from pokemon_valuator.integrations.tcgdex_public import prune_tcgdex_detail

                        detail = get_card_detail(chosen.id, timeout_sec=8)
                        clean = prune_tcgdex_detail(detail)

                        debug["pricing"]["tcgdex"] = {
                            "status": "success",
                            "match": {"id": chosen.id, "name": chosen.name, "localId": chosen.localId},
                            "detail": clean,
                        }
            except Exception as e:
                debug["pricing"]["tcgdex"] = {"status": "error", "error": str(e)}
            
            try:
                from pokemon_valuator.integrations.tcgdex_public import search_cards, get_card_detail

                nm = (final_card or {}).get("name")
                if not nm or not number_only:
                    debug["pricing"]["tcgdex"] = {
                        "status": "missing_fields",
                        "name": nm,
                        "localId": number_only,
                    }
                else:
                    hits = search_cards(name=str(nm), local_id=str(number_only), timeout_sec=8)

                    if not hits:
                        debug["pricing"]["tcgdex"] = {
                            "status": "not_found",
                            "query": {"name": nm, "localId": str(number_only)},
                        }
                    else:
                        target_set = ((final_card.get("set") or {}).get("name") or "").strip().lower()
                        chosen = hits[0]

                        if len(hits) > 1 and target_set:
                            for h in hits[:3]:
                                try:
                                    d = get_card_detail(h.id, timeout_sec=8)
                                    setname = ((d.get("set") or {}).get("name") or "").strip().lower()
                                    if setname and setname == target_set:
                                        chosen = h
                                        break
                                except Exception:
                                    continue

                        from pokemon_valuator.integrations.tcgdex_public import prune_tcgdex_detail

                        detail = get_card_detail(chosen.id, timeout_sec=8)
                        clean = prune_tcgdex_detail(detail)
                        clean = _tcgdex_force_usd(clean, eur_to_usd=1.10)
                        debug["pricing"]["tcgdex"] = {
                            "status": "success",
                            "match": {"id": chosen.id, "name": chosen.name, "localId": chosen.localId},
                            "detail": clean,
                        }

            except Exception as e:
                debug["pricing"]["tcgdex"] = {"status": "error", "error": str(e)}

        # 1) Title OCR (loose)
        _progress("OCR (Title)", "Reading card name…")
        name_norm = self._ocr_title_yolo(img, debug["ocr"])
        name_token = self._clean_name_token(name_norm)

        debug["ocr"]["name_norm"] = name_norm
        debug["ocr"]["name_token"] = name_token

        # 2) Number OCR
        # returns: (frac_tuple, fraction_raw, number_only, number_source, frac_plaus)
        _progress("OCR (Number)", "Reading card number…")
        frac_tuple, fraction_raw, number_only, number_source, frac_plaus = self._ocr_number_yolo(img, debug["ocr"])
        debug["ocr"]["number_source"] = number_source
        debug["ocr"]["fraction_plaus_score"] = frac_plaus

        # Set code OCR (modern cards)
        _progress("OCR (Set code)", "Reading set symbol/code…")
        set_code = self._ocr_set_code(img, debug["ocr"])
        debug["ocr"]["set_code"] = set_code
        self.setcode_ocr = set_code


        # Accept fraction if plausibility decent
        frac: Optional[Tuple[int, int]] = None
        use_frac = (frac_tuple is not None) and (frac_plaus is not None) and (frac_plaus >= 10)
        if use_frac:
            frac = frac_tuple
        else:
            fraction_raw = None

        card_number_str = f"{frac[0]}/{frac[1]}" if frac else None
        printed_total = frac[1] if frac else None

        debug["ocr"]["fraction_raw"] = fraction_raw
        debug["ocr"]["number_only"] = number_only
        debug["ocr"]["card_number"] = card_number_str
        debug["ocr"]["printed_total"] = printed_total

        print("NUMBER_DEBUG:", debug["ocr"].get("number_yolo") or debug["ocr"].get("number_fallback"))

        # 3) Small-number safety
        if frac is None and number_only is not None and number_only <= 20 and not name_token and not set_code:
            debug["pricing"]["pricecharting"] = {
                "status": "skipped_low_info",
                "reason": "no_fraction_and_small_number_only",
                "number_only": number_only,
            }
            return IdentificationResult(
                status="failed",
                card_id=None,
                card_name=None,
                set_name=None,
                card_number=card_number_str,
                printed_total=printed_total,
                tcgplayer_id=None,
                confidence=0.0,
                method="refuse_ambiguous_number_only",
                debug=debug,
            )
        # 4) FAST PATH: set_code + number (highest accuracy for modern)
        fast_candidates: List[Dict] = []
        if set_code and number_only is not None:
            _progress("Candidate search", "Searching by set code + number…")
        
        # 5) API candidates (fallback to your usual logic)
        # Prefer fast_candidates if present
        _progress("Candidate search", "Querying PokémonTCG API candidates…")
        candidates = fast_candidates or self._api_search_candidates(
            frac=frac,
            number_only=number_only,
            name_token=name_token,
        )
        debug["api"]["candidate_count"] = len(candidates)
        debug["api"]["query_debug"] = getattr(self, "_last_api_debug", {})
        
        if candidates and name_token:
            nt = name_token.strip().lower()
            filtered = [
                c for c in candidates
                if nt in (str(c.get("name") or "").lower())
            ]

            debug["api"]["candidate_count_before_name_filter"] = len(candidates)
            debug["api"]["candidate_count_after_name_filter"] = len(filtered)

            # Only replace if we didn't filter everything out
            if filtered:
                candidates = filtered

        if (
            set_code
            and frac is not None
            and number_only is not None
            and isinstance(candidates, list)
            and len(candidates) > 1
        ):
            if set_code.upper() == "MEG":
                debug["api"]["setcode_narrowing"] = {
                    "used": False,
                    "reason": "set_code MEG (likely not in PokemonTCG official API)",
                    "set_code": set_code,
                    "before": len(candidates),
                }
            else:
                try:
                    narrowed = self._api_search_by_set_code_number(
                        set_code=set_code,
                        number_only=number_only,
                        frac=frac,
                    )
                    cand_ids = {c.get("id") for c in candidates if c.get("id")}
                    narrowed_ids = {c.get("id") for c in (narrowed or []) if c.get("id")}

                    intersection_ids = cand_ids.intersection(narrowed_ids)

                    if intersection_ids:
                        before = len(candidates)
                        candidates = [c for c in candidates if c.get("id") in intersection_ids]
                        after = len(candidates)

                        debug["api"]["setcode_narrowing"] = {
                            "used": True,
                            "set_code": set_code,
                            "before": before,
                            "after": after,
                            "intersection": True,
                        }
                        debug["api"]["candidate_count"] = len(candidates)  # update count
                    else:
                        debug["api"]["setcode_narrowing"] = {
                            "used": False,
                            "set_code": set_code,
                            "before": len(candidates),
                            "narrowed_count": len(narrowed or []),
                            "reason": "no_intersection_with_current_candidates",
                        }

                except Exception as e:
                    debug["api"]["setcode_narrowing"] = {
                        "used": False,
                        "set_code": set_code,
                        "before": len(candidates),
                        "error": str(e),
                    }

        if not candidates:
            debug["pricing"]["pricecharting"] = {
                "status": "skipped_no_candidates",
                "card_number": card_number_str,
                "name_norm": name_norm,
                "set_code": set_code,
            }
            return IdentificationResult(
                status="failed",
                card_id=None,
                card_name=None,
                set_name=None,
                card_number=card_number_str,
                printed_total=printed_total,
                tcgplayer_id=None,
                confidence=0.0,
                method="api_failed",
                debug=debug,
            )

        # 5.5) single candidate shortcut (fraction-only)
        if len(candidates) == 1 and frac is not None:
            only = candidates[0]
            final_card = _fetch_full_card_if_possible(only)
            tcgplayer_id = self._extract_tcgplayer_id(final_card) or self._extract_tcgplayer_id(only)

            _attach_pricing_for_final_card(final_card)

            return IdentificationResult(
                status="success",
                card_id=final_card.get("id") or only.get("id"),
                card_name=final_card.get("name"),
                set_name=(final_card.get("set") or {}).get("name"),
                card_number=card_number_str,
                printed_total=printed_total,
                tcgplayer_id=tcgplayer_id,
                confidence=0.90,
                method="api_single_candidate_fraction_only",
                debug=debug,
            )

        # 6) Visual verify (only needed if multiple candidates)
        _progress("Matching artwork", "Comparing against reference images…")
        scored = self._verify_candidates_visually(img, candidates)
        debug["visual"]["api_candidates_scored"] = [(cid, float(sim)) for cid, sim, _ in scored[:5]]

        if not scored:
            top = candidates[0]
            final_card = _fetch_full_card_if_possible(top)
            tcgplayer_id = self._extract_tcgplayer_id(final_card) or self._extract_tcgplayer_id(top)

            _attach_pricing_for_final_card(final_card)

            return IdentificationResult(
                status="uncertain",
                card_id=final_card.get("id") or top.get("id"),
                card_name=final_card.get("name"),
                set_name=(final_card.get("set") or {}).get("name"),
                card_number=card_number_str,
                printed_total=printed_total,
                tcgplayer_id=tcgplayer_id,
                confidence=0.50,
                method="api_candidates_no_visual_scores",
                debug=debug,
            )

        # 7) Number-aware rerank
        hint_a = number_only
        hint_b = printed_total

        reranked: List[Tuple[float, float, str, Dict]] = []
        for cid, sim, c in scored[:10]:
            cand_a, cand_b = self._candidate_number_tuple(c)
            ns = self._number_hint_score(hint_a, hint_b, cand_a, cand_b)
            reranked.append((float(sim), float(ns), cid, c))

        reranked.sort(key=lambda x: (x[0] + 0.12 * x[1]), reverse=True)

        debug["visual"]["rerank_top"] = [
            {
                "id": cid,
                "sim": float(sim),
                "num_score": float(ns),
                "cand_num": self._candidate_number_tuple(c)[0],
                "cand_total": self._candidate_number_tuple(c)[1],
            }
            for sim, ns, cid, c in reranked[:5]
        ]
        best_sim, best_numscore, best_id, best_card = reranked[0]

        final_card = _fetch_full_card_if_possible(best_card)
        tcgplayer_id = self._extract_tcgplayer_id(final_card) or self._extract_tcgplayer_id(best_card)

        _attach_pricing_for_final_card(final_card)

        status = "success" if best_sim >= self.success_threshold else "uncertain"
        method = "api_candidate_visual_verify"
        if best_sim < self.uncertain_threshold:
            method = "api_candidate_visual_verify_low_confidence"
        
        _progress("Done", "Scan complete ✅")
        return IdentificationResult(
            status=status,
            card_id=final_card.get("id") or best_id,
            card_name=final_card.get("name"),
            set_name=(final_card.get("set") or {}).get("name"),
            card_number=card_number_str,
            printed_total=printed_total,
            tcgplayer_id=tcgplayer_id,
            confidence=float(best_sim),
            method=method,
            debug=debug,
        )
