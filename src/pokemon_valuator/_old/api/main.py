################################################################################
# FILE: src/pokemon_valuator/api/main.py
################################################################################
# PURPOSE: FastAPI service exposing the valuator pipeline.
#
# Endpoints:
# - GET  /health
# - POST /valuate  (multipart/form-data with an image file)
#
# Notes:
# - Keeps business logic out of the API layer.
# - The API returns structured JSON for frontend use.
################################################################################

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI, File, UploadFile, HTTPException

from src.pokemon_valuator.pipeline.prediction_pipeline_final import PokemonCardValuator

app = FastAPI(title="Pokemon Card Valuator", version="1.0")

valuator = PokemonCardValuator()

TMP_DIR = Path("data/interim/uploads")
TMP_DIR.mkdir(parents=True, exist_ok=True)

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok"}

@app.post("/valuate")
def valuate(image: UploadFile = File(...)) -> Dict[str, Any]:
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="Upload a JPG/PNG/WebP image.")

    tmp_path = TMP_DIR / image.filename
    with tmp_path.open("wb") as f:
        shutil.copyfileobj(image.file, f)

    try:
        result = valuator.valuate(str(tmp_path))
        return result
    finally:
        # In a real service you might keep uploads for debugging with retention rules.
        if tmp_path.exists():
            tmp_path.unlink()
