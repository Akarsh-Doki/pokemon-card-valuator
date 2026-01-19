from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pokemon_valuator.integrations.pricecharting_public import fetch_price_history

from api.jobs import create_job, get_job, push, push_error, push_result

# Allow importing your package without "pip install -e ."
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pokemon_valuator.models.card_identifier import CardIdentifier 


app = FastAPI(title="Pokemon Valuator API", version="1.0")

# CORS for local frontend dev (vite default)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CI = CardIdentifier(
    cards_reference_csv=str(ROOT / "data/raw/pokemon_tcg_api/cards_reference.csv"),
    reference_images_dir=str(ROOT / "data/raw/pokemon_tcg_api/reference_images"),
    yolo_weights_path=str(ROOT / "runs/yolo_regions/regions2/weights/best.pt"),
)

FEEDBACK_PATH = ROOT / "api" / "cache_approvals.json"


def _read_feedback() -> Dict[str, Any]:
    if not FEEDBACK_PATH.exists():
        return {"approvals": []}
    try:
        return json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"approvals": []}


def _write_feedback(obj: Dict[str, Any]) -> None:
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK_PATH.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/progress/{job_id}")
async def progress(job_id: str):
    st = get_job(job_id)
    if not st:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_gen():
        yield (
            "event: progress\ndata: "
            + json.dumps({"stage": "Starting", "detail": "Preparing scan…"})
            + "\n\n"
        )

        while True:
            msg = await st.queue.get()

            if msg["type"] == "progress":
                yield (
                    "event: progress\ndata: "
                    + json.dumps(
                        {"stage": msg.get("stage") or "", "detail": msg.get("detail") or ""}
                    )
                    + "\n\n"
                )

            elif msg["type"] == "error":
                yield "event: error\ndata: " + json.dumps(
                    {"message": msg.get("message") or "Scan failed"}
                ) + "\n\n"
                break

            elif msg["type"] == "result":
                yield "event: result\ndata: " + json.dumps(msg["payload"]) + "\n\n"
                break

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# ---------- Identify (async job) ----------
@app.post("/identify_async")
async def identify_async(file: UploadFile = File(...)) -> Dict[str, str]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")

    b = await file.read()
    img_hash = _sha256_bytes(b)

    # create job + save file
    st = create_job()

    tmp_dir = ROOT / "api" / "tmp_uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # keep original extension if possible
    filename = file.filename.replace("/", "_").replace("\\", "_")
    tmp_path = tmp_dir / f"{st.job_id}_{filename}"
    tmp_path.write_bytes(b)

    # run job in background
    asyncio.create_task(_run_identify_job(st.job_id, str(tmp_path), img_hash))

    return {"job_id": st.job_id}


async def _run_identify_job(job_id: str, image_path: str, img_hash: str) -> None:
    """
    Runs CardIdentifier.identify(...) in a thread and streams REAL progress
    using identify(progress_cb=...).
    """
    try:
        # Push a starter stage (UI instant feedback)
        await push(job_id, "Starting", "Uploading image…")

        loop = asyncio.get_running_loop()

        # This callback will run inside the worker thread, so we must push using thread-safe scheduling.
        def cb(stage: str, detail: str = "") -> None:
            try:
                asyncio.run_coroutine_threadsafe(push(job_id, stage, detail), loop)
            except Exception:
                # never break identification due to progress streaming errors
                pass

        # Run identify (blocking CPU/API work) in executor thread
        res = await loop.run_in_executor(
            None,
            lambda: CI.identify(image_path, progress_cb=cb),
        )

        debug = res.debug or {}

        if not isinstance(debug.get("pricing"), dict):
            debug["pricing"] = {}

        if isinstance(debug.get("pricecharting"), dict) and "pricecharting" not in debug["pricing"]:
            debug["pricing"]["pricecharting"] = debug["pricecharting"]

        if isinstance(debug.get("tcgdex"), dict) and "tcgdex" not in debug["pricing"]:
            debug["pricing"]["tcgdex"] = debug["tcgdex"]

        payload = {
            "status": res.status,
            "confidence": float(res.confidence or 0.0),
            "method": res.method,
            "card_id": res.card_id,
            "card_name": res.card_name,
            "set_name": res.set_name,
            "card_number": res.card_number,
            "debug": debug,
            "image_hash": img_hash,
        }

        await push_result(job_id, payload)

    except Exception as e:
        await push_error(job_id, "Scan failed. Please try a clearer photo.")

    finally:
        # cleanup temp file
        try:
            os.remove(image_path)
        except Exception:
            pass


@app.post("/feedback")
async def feedback(payload: Dict[str, Any]) -> Dict[str, str]:
    """
    payload:
      {
        "image_hash": "...",
        "correct": true/false,
        "chosen_variant_url": "..."
      }
    """
    image_hash = str(payload.get("image_hash") or "")
    correct = bool(payload.get("correct"))
    chosen_variant_url = str(payload.get("chosen_variant_url") or "")

    if not image_hash:
        raise HTTPException(status_code=400, detail="missing image_hash")

    if correct:
        db = _read_feedback()
        db["approvals"].append(
            {
                "image_hash": image_hash,
                "chosen_variant_url": chosen_variant_url,
            }
        )
        _write_feedback(db)
        return {"status": "saved"}

    return {"status": "ignored"}

@app.get("/price_history")
def price_history(url: str) -> Dict[str, Any]:
    if not url:
        raise HTTPException(status_code=400, detail="missing url")

    try:
        return fetch_price_history(url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"history fetch failed: {str(e)}")


@app.get("/cache")
def cache() -> Dict[str, Any]:
    return _read_feedback()