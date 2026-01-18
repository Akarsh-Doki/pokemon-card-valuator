from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List

import cv2
import numpy as np

from src.pokemon_valuator.utils.image_io import read_image_bgr


@dataclass(frozen=True)
class ImageQualityConfig:
    min_width: int = 600
    min_height: int = 600
    blur_var_threshold: float = 60.0  # Laplacian variance below this is often blurry
    too_dark_mean_l: float = 35.0
    too_bright_mean_l: float = 235.0
    glare_frac_threshold: float = 0.12  # fraction of specular-like pixels


class ImageQualityValidator:
    def __init__(self, cfg: ImageQualityConfig | None = None):
        self.cfg = cfg or ImageQualityConfig()

    def validate(self, image_path: str) -> Dict[str, Any]:
        img = read_image_bgr(image_path)
        h, w = img.shape[:2]
        issues: List[str] = []

        if w < self.cfg.min_width or h < self.cfg.min_height:
            issues.append(f"Image resolution is low ({w}x{h}). Try a closer, higher-resolution photo.")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if blur_var < self.cfg.blur_var_threshold:
            issues.append("Image looks blurry. Hold the camera steady and ensure focus.")

        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        L, A, B = cv2.split(lab)
        mean_l = float(np.mean(L))
        if mean_l < self.cfg.too_dark_mean_l:
            issues.append("Image is too dark. Improve lighting.")
        if mean_l > self.cfg.too_bright_mean_l:
            issues.append("Image is overexposed. Reduce direct light / move away from glare.")

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        H, S, V = cv2.split(hsv)
        glare = (V > 245) & (S < 25)
        glare_frac = float(glare.mean())
        if glare_frac > self.cfg.glare_frac_threshold:
            issues.append("Strong glare detected. Tilt card slightly or change lighting.")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "metrics": {
                "width": w,
                "height": h,
                "blur_var": blur_var,
                "mean_l": mean_l,
                "glare_frac": glare_frac,
            },
        }
