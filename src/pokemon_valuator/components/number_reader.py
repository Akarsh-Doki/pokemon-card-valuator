from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    from paddleocr import PaddleOCR  # type: ignore
except Exception:
    PaddleOCR = None

CARD_NO_RE = re.compile(r"(\d{1,3})\s*/\s*(\d{1,4})")

@dataclass(frozen=True)
class NumberReadResult:
    frac: Optional[Tuple[int, int]]
    frac_raw: Optional[str]
    number_only: Optional[int]
    debug: Dict


class PaddleNumberReader:
    DIGITISH_RE = re.compile(r"[0-9/]+")

    def __init__(self):
        if PaddleOCR is None:
            raise RuntimeError("paddleocr not installed")

        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")

        self.ocr = PaddleOCR(lang="en")

    def _pil_to_bgr(self, img: Image.Image) -> np.ndarray:
        rgb = np.array(img.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def _parse_predict(self, result) -> List[Tuple[str, float]]:
        out: List[Tuple[str, float]] = []
        if not result:
            return out

        for item in result:
            if item is None:
                continue

            if isinstance(item, dict):
                texts = item.get("rec_texts") or item.get("texts") or []
                scores = item.get("rec_scores") or item.get("scores") or []
                if texts and scores and len(texts) == len(scores):
                    for t, s in zip(texts, scores):
                        out.append((str(t), float(s)))
                    continue

            for key_text, key_score in [("rec_texts", "rec_scores"), ("texts", "scores")]:
                if hasattr(item, key_text) and hasattr(item, key_score):
                    ts = getattr(item, key_text)
                    ss = getattr(item, key_score)
                    for t, s in zip(ts, ss):
                        out.append((str(t), float(s)))
                    break

        return out

    def _parse_ocr(self, result) -> List[Tuple[str, float]]:
        out: List[Tuple[str, float]] = []
        if not result:
            return out

        for line in result:
            for item in line:
                if not item or len(item) < 2:
                    continue
                txt = item[1][0]
                sc = float(item[1][1])
                out.append((str(txt), sc))

        return out

    def _keep_digitish(self, s: str) -> str:
        if not s:
            return ""
        t = "".join(ch for ch in s if ch.isdigit() or ch == "/")
        t = re.sub(r"/{2,}", "/", t)
        return t

    def _score_fraction(self, a: int, b: int) -> int:
        if a <= 0 or b <= 0:
            return -999
        score = 0
        if 20 <= b <= 400:
            score += 8
        elif 10 <= b <= 600:
            score += 4
        else:
            score -= 8
        if 1 <= a <= 400:
            score += 3
        else:
            score -= 6
        if a <= b:
            score += 3
        if b < 10:
            score -= 10
        return score

    def _extract_best_fraction_from_texts(self, texts: List[Tuple[str, float]]) -> Tuple[Optional[Tuple[int, int]], Optional[str], Dict]:
        dbg: Dict = {}

        digitish_tokens: List[str] = []
        for t, _ in texts:
            dt = self._keep_digitish(t)
            if self.DIGITISH_RE.search(dt):
                digitish_tokens.append(dt)

        joined = " ".join(digitish_tokens).strip()
        dbg["digitish_joined"] = joined

        best: Optional[Tuple[int, int]] = None
        best_raw: Optional[str] = None
        best_score = -10**9

        def scan_string(s: str):
            nonlocal best, best_raw, best_score
            if not s:
                return
            for m in CARD_NO_RE.finditer(s):
                a = int(m.group(1))
                b = int(m.group(2))
                sc = self._score_fraction(a, b)
                if sc > best_score:
                    best = (a, b)
                    best_raw = f"{a}/{b}"
                    best_score = sc

        for tok in digitish_tokens:
            scan_string(tok)

        scan_string(joined)

        if best is None:
            digits = "".join(ch for ch in joined if ch.isdigit())
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
                    if b > 600:
                        continue
                    candidates.append((a, b))
            for a, b in candidates:
                sc = self._score_fraction(a, b)
                if sc > best_score:
                    best = (a, b)
                    best_raw = f"{a}/{b}"
                    best_score = sc

        dbg["best_score"] = best_score
        return best, best_raw, dbg

    def read(self, crop: Image.Image) -> NumberReadResult:
        crop = ImageOps.exif_transpose(crop).convert("RGB")
        bgr = self._pil_to_bgr(crop)

        debug: Dict = {}

        used = None
        texts: List[Tuple[str, float]] = []
        try:
            raw = self.ocr.predict(bgr)
            used = "predict"
            texts = self._parse_predict(raw)
        except Exception as e:
            debug["predict_error"] = str(e)
            raw = self.ocr.ocr(bgr)
            used = "ocr"
            texts = self._parse_ocr(raw)

        debug["paddle_used"] = used
        texts_sorted = sorted(texts, key=lambda x: x[1], reverse=True)
        debug["paddle_top"] = [{"text": t, "score": float(s)} for t, s in texts_sorted[:6]]

        frac, frac_raw, dbg2 = self._extract_best_fraction_from_texts(texts_sorted)
        debug.update(dbg2)

        if frac is None:
            debug["digitish_join_failed"] = True
            return NumberReadResult(frac=None, frac_raw=None, number_only=None, debug=debug)

        a, b = frac
        debug["fraction_plausibility"] = {"a": a, "b": b, "score": self._score_fraction(a, b)}
        debug["fraction_raw"] = frac_raw

        return NumberReadResult(frac=(a, b), frac_raw=frac_raw, number_only=a, debug=debug)
