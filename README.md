# 🎴 Pokémon Card Valuator

This project turns a user photo of a Pokémon card into:

1) **Card identification** (canonical card_id)  
2) **PSA grade prediction** (ML)  
3) **Market data lookup** (ungraded + PSA 7/8/9/10 if available)

## Key design decisions (portfolio-grade)
- **API-first pricing**: We build a *local snapshot* from a pricing API for fast runtime lookups and reproducibility.
- **eBay for training labels**: We use eBay sold listings primarily to collect **graded images + grade labels** for training the grader model.
- **No multipliers / no ROI advice** in runtime: the system returns **data**, not business recommendations.

## Quickstart

### 1) Setup environment
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Add secrets
Copy `config/secrets.example.yaml` to `config/secrets.yaml` and fill:
- `pokemonpricetracker_api_key`
- `ebay_app_id`

### 3) Build price snapshot (API)
```bash
python scripts/build_price_db_from_api.py
```

### 4) Collect training images (eBay graded images)
```bash
python scripts/collect_training_images.py
```

### 5) Train grader model (placeholder script)
```bash
python scripts/train_grader.py
```

### 6) Run FastAPI
```bash
uvicorn src.pokemon_valuator.api.main:app --reload
```

Then open:
- `http://127.0.0.1:8000/docs`

## DVC
This repo includes a `dvc.yaml` and `params.yaml` skeleton so you can version:
- dataset snapshots
- model artifacts
- evaluation reports

Run:
```bash
dvc repro
```

## Notebooks
See `/notebooks` for step-by-step explanations:
- data collection
- snapshot building
- training plan
- evaluation and calibration


## Optional: Download prebuilt assets (Google Drive + gdown)

This repo is designed so reviewers can run a demo without re-collecting data or retraining.

1) Copy the template manifest:

```bash
cp config/assets.yaml.example config/assets.yaml
```

2) Edit `config/assets.yaml` and paste your Google Drive file IDs/URLs.

3) Download assets:

```bash
python scripts/download_assets.py --all
```

What this can download:
- pricing snapshot CSV (runtime lookup)
- visual retrieval index (`card_index.json`)
- trained grader weights (`best_model.h5`)
- optional sample images

> Note: `config/assets.yaml` should NOT be committed (it contains your personal links).


## YOLO regions (title / card_number / set_symbol)

This repo supports a YOLOv8 region detector to make OCR reliable by cropping only:
- title
- card_number
- set_symbol

### Train (your labeled dataset)
```bash
pip install ultralytics
python scripts/train_yolo_regions.py --data /absolute/path/to/pokemon-dataset/data.yaml --epochs 150 --imgsz 960
```

### Run with YOLO enabled
```bash
export YOLO_REGIONS_WEIGHTS=/absolute/path/to/runs/yolo_regions/regions/weights/best.pt
python scripts/test_region_id.py --image test_images/<your_photo>.jpg
```

If `YOLO_REGIONS_WEIGHTS` is not set, the system falls back to heuristic OCR crops (less reliable).

## Demo

![Home](docs/screenshots/homepage.png)
![Result](docs/screenshots/resultspage.png)
