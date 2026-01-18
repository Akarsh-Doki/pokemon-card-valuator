from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, Optional

from PIL import Image, ImageDraw, ImageOps

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


@dataclass(frozen=True)
class Box:
    xyxy: Tuple[int, int, int, int]  # (x1,y1,x2,y2)
    conf: float
    cls_name: str


class YOLORegionDetector:
    """
    Detect regions using YOLO class names, then optionally remap them.

    Canonical class names:
      - title
      - card_number
      - set_symbol
    """
    CANONICAL = {"title", "card_number", "set_symbol"}

    def __init__(
        self,
        weights_path: str,
        conf: float = 0.25,
        iou: float = 0.5,
        class_remap: Optional[Dict[str, str]] = None,
    ):
        if YOLO is None:
            raise ImportError(
                "ultralytics is not installed. Install it with `pip install ultralytics` "
                "(or disable YOLO by passing yolo_weights_path=None)."
            )
        self.model = YOLO(weights_path)
        self.conf = float(conf)
        self.iou = float(iou)

        self.class_remap = {str(k).lower(): str(v).lower() for k, v in (class_remap or {}).items()}

        self.expected_centers = {
            "title": (0.50, 0.12),
            "card_number": (0.82, 0.93),
            "set_symbol": (0.20, 0.92),
        }

    def _norm_name(self, raw_name: str) -> str:
        s = (raw_name or "").strip().lower()
        s = s.replace(" ", "_")
        s = "".join(ch for ch in s if (ch.isalnum() or ch == "_"))
        return s

    def _center(self, xyxy: Tuple[int, int, int, int]) -> Tuple[float, float]:
        x1, y1, x2, y2 = xyxy
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    def _score_box(self, img_w: int, img_h: int, cls_name: str, conf: float, xyxy: Tuple[int, int, int, int]) -> float:
        """
        Score = conf - distance_penalty

        Distance penalty makes sure we keep the correct box when YOLO fires multiple boxes
        or swaps classes occasionally.
        """
        cx, cy = self._center(xyxy)

        ex, ey = self.expected_centers.get(cls_name, (0.5, 0.5))
        ex *= img_w
        ey *= img_h

        dx = (cx - ex)
        dy = (cy - ey)
        dist = (dx * dx + dy * dy) ** 0.5
        diag = (img_w * img_w + img_h * img_h) ** 0.5
        dist_norm = dist / (diag + 1e-9)
        return float(conf) - 0.35 * float(dist_norm)

    def detect(self, img: Image.Image) -> Dict[str, Box]:
        img = ImageOps.exif_transpose(img)

        res = self.model.predict(img, conf=self.conf, iou=self.iou, verbose=False)
        if not res:
            return {}

        r0 = res[0]
        names = r0.names or {}
        if r0.boxes is None:
            return {}

        w, h = img.size

        out: Dict[str, Box] = {}
        best_score: Dict[str, float] = {}

        for b in r0.boxes:
            cls_id = int(b.cls.item())
            raw_name = self._norm_name(str(names.get(cls_id, cls_id)))
            cls_name = self.class_remap.get(raw_name, raw_name)

            if cls_name not in self.CANONICAL:
                continue

            conf = float(b.conf.item())
            x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
            xyxy = (x1, y1, x2, y2)

            s = self._score_box(w, h, cls_name, conf, xyxy)

            if (cls_name not in out) or (s > best_score.get(cls_name, -1e9)):
                out[cls_name] = Box(xyxy=xyxy, conf=conf, cls_name=cls_name)
                best_score[cls_name] = s

        return out

    def crop(self, img: Image.Image, box: Box, pad: int = 8) -> Image.Image:
        img = ImageOps.exif_transpose(img)

        w, h = img.size
        x1, y1, x2, y2 = box.xyxy
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        return img.crop((x1, y1, x2, y2))

    def draw(self, img: Image.Image, dets: Dict[str, Box]) -> Image.Image:
        img = ImageOps.exif_transpose(img)

        out = img.copy()
        d = ImageDraw.Draw(out)
        for k, b in dets.items():
            x1, y1, x2, y2 = b.xyxy
            d.rectangle([x1, y1, x2, y2], outline="red", width=4)
            d.text((x1 + 6, y1 + 6), f"{k} {b.conf:.2f}", fill="red")
        return out
