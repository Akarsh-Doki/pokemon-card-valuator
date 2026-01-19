from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional

import numpy as np
import cv2

from src.pokemon_valuator.utils.image_io import read_image_bgr
try:
    import pytesseract
except Exception:
    pytesseract = None


@dataclass(frozen=True)
class VariantResult:
    is_first_edition: Optional[bool]
    is_shadowless: Optional[bool]
    is_holo: Optional[bool]
    method: str
    debug: Dict[str, Any]


class CardVariantDetector:
    def detect(self, image_path: str, card_id: str | None = None) -> Dict[str, Any]:
        img = read_image_bgr(image_path)
        h, w = img.shape[:2]
        debug: Dict[str, Any] = {}

        is_first = None
        if pytesseract is not None:
            crop = img[int(h * 0.68): int(h * 0.92), int(w * 0.02): int(w * 0.28)]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
            gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            text = pytesseract.image_to_string(gray)
            debug["first_edition_ocr"] = text.strip()
            if "1st" in text.lower() and "edition" in text.lower():
                is_first = True
            elif text.strip() != "":
                # If OCR returned something but not the phrase, lean False (still uncertain)
                is_first = False
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        _, S, V = cv2.split(hsv)
        spec = (V > 245) & (S < 35)
        spec_frac = float(spec.mean())
        debug["specular_frac"] = spec_frac
        is_holo = True if spec_frac > 0.06 else None

        is_shadowless = None

        method = "ocr+specular" if pytesseract is not None else "specular_only"
        return {
            "is_first_edition": is_first,
            "is_shadowless": is_shadowless,
            "is_holo": is_holo,
            "method": method,
            "debug": debug,
            "note": "Baseline detector; returns None when uncertain.",
        }
