from __future__ import annotations

import cv2
import numpy as np

def load_bgr(path: str) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return img

def read_image_bgr(path: str) -> np.ndarray:
    """Alias for load_bgr()."""
    return load_bgr(path)
