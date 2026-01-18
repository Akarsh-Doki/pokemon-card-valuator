from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

def _expand(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))

def resolve_yolo_weights(
    explicit_path: Optional[str] = None,
    config_path: Optional[str] = None,
    env_var: str = "YOLO_REGIONS_WEIGHTS",
    runs_root: str = "runs/yolo_regions",
) -> Optional[str]:
    if explicit_path:
        p = _expand(explicit_path)
        return p if os.path.exists(p) else None

    env_val = os.environ.get(env_var)
    if env_val:
        p = _expand(env_val)
        return p if os.path.exists(p) else None

    if config_path and os.path.exists(config_path):
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            y = cfg.get("yolo", {}) or {}
            w = y.get("weights_path")
            if w:
                p = _expand(w)
                return p if os.path.exists(p) else None
        except Exception:
            pass

    rr = Path(runs_root)
    if rr.exists():
        bests = list(rr.glob("**/weights/best.pt"))
        if bests:
            bests.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(bests[0].resolve())

    return None