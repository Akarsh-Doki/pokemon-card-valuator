################################################################################
# FILE: src/pokemon_valuator/pipeline/prediction_pipeline_final.py
################################################################################
# PURPOSE: Image -> Identify -> Grade -> Price lookup (API-first)
#
# Portfolio-grade principles implemented:
# - Canonical card IDs come from a reference table (PokemonTCG API) + visual retrieval.
# - Grade prediction is ML output; we surface probabilities + interpretable features.
# - Pricing is a lookup from a *local snapshot* built from a pricing API.
# - No ROI recommendations, no "should you grade" advice, and no hidden multipliers.
################################################################################

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any
import pandas as pd

import numpy as np
import yaml
import logging

import tensorflow as tf
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing import image as keras_image

from src.pokemon_valuator.utils.image_quality_validator import ImageQualityValidator
from src.pokemon_valuator.models.card_identifier import CardIdentifier, IdentificationResult
from src.pokemon_valuator.components.card_variant_detector import CardVariantDetector
from src.pokemon_valuator.utils.psa_feature_extractor import PSAFeatureExtractor
from src.pokemon_valuator.components.simple_price_lookup import SimplePriceLookup


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PokemonCardValuator:
    """High-level orchestrator.

    This class composes independent components:
    - ImageQualityValidator
    - CardIdentifier (OCR + visual retrieval)
    - PSAFeatureExtractor (interpretable engineered features)
    - PSA grade model (CNN + engineered features)
    - SimplePriceLookup (local API snapshot)
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        # --- image quality gate
        self.image_validator = ImageQualityValidator()

        # --- identification
        yolo_weights = os.environ.get("YOLO_REGIONS_WEIGHTS")

        self.card_identifier = CardIdentifier(
            cards_reference_csv=str(Path(self.config["raw_data"]) / "pokemon_tcg_api" / "cards_reference.csv"),
            reference_images_dir=str(Path(self.config["raw_data"]) / "pokemon_tcg_api" / "reference_images"),
            index_path=str(Path(self.config["processed_data"]) / "identification" / "card_index.json"),
            yolo_weights_path=yolo_weights,
        )

        # --- variants (attributes only; safe None values if uncertain)
        self.variant_detector = CardVariantDetector()

        # --- engineered features for grading + explainability
        self.feature_extractor = PSAFeatureExtractor()

        # --- pricing lookup (local snapshot)
        self.price_lookup = SimplePriceLookup(config_path=config_path)

        # --- grade model
        model_path = Path(self.config["models_dir"]) / "psa_grader_v2" / "best_model.h5"
        if not model_path.exists():
            raise FileNotFoundError(
                f"Missing grader model at {model_path}. "
                f"Train/export it first (see notebooks and src/pokemon_valuator/models/psa_grader_model.py)."
            )

        self.psa_grader = load_model(
            model_path,
            custom_objects={
                "ordinal_crossentropy_loss": self._ordinal_loss,
                "mae_grades": self._mae_grades,
                "within_1_grade_acc": self._within_1_acc,
            },
        )

        # Grade mapping config (avoids off-by-one bugs)
        grade_cfg = self.config.get("psa_grade_model", {})
        self.grade_min = int(grade_cfg.get("grade_min", 1))
        self.grade_max = int(grade_cfg.get("grade_max", 10))

        self.cards_ref = pd.read_csv(
            Path(self.config["raw_data"]) / "pokemon_tcg_api" / "cards_reference.csv"
        )

    def valuate(self, image_path: str) -> Dict[str, Any]:
        image_path = str(image_path)
        logger.info("VALUATING: %s", Path(image_path).name)

        # 0) Validate image quality (fail fast with actionable feedback)
        validation = self.image_validator.validate(image_path)
        if not validation.get("valid", False):
            return {
                "status": "invalid_image",
                "issues": validation.get("issues", []),
                "metrics": validation.get("metrics", {}),
            }

        # 1) Identify card
        id_result = self.card_identifier.identify(image_path)
        if isinstance(id_result, IdentificationResult):
            id_dict = asdict(id_result)
        else:
            # If you later refactor identify() to return dicts, this remains safe.
            id_dict = dict(id_result)

        if id_dict.get("status") != "success" or not id_dict.get("card_id"):
            return {
                "status": "failed",
                "message": "Could not identify the card reliably.",
                "identification": id_dict,
            }

        card_id = str(id_dict["card_id"])

        ref_row = self.cards_ref[self.cards_ref["card_id"] == card_id]
        tcgplayer_id = None
        if not ref_row.empty and "tcgplayer_id" in ref_row.columns:
            v = ref_row.iloc[0].get("tcgplayer_id")
            if pd.notna(v):
                tcgplayer_id = int(v)


        # 2) Variant attributes (optional; can be None)
        variants = self.variant_detector.detect(image_path, card_id=card_id)

        # 3) Engineered grading features
        feats = self.feature_extractor.extract(image_path)
        feat_vec = self.feature_extractor.to_array(feats)

        # 4) Predict PSA grade
        img = keras_image.load_img(image_path, target_size=(224, 224))
        img_array = keras_image.img_to_array(img) / 255.0
        img_batch = np.expand_dims(img_array, axis=0)
        feat_batch = np.expand_dims(feat_vec, axis=0)

        probs = self.psa_grader.predict([img_batch, feat_batch], verbose=0)[0]
        probs = np.asarray(probs, dtype=float)

        predicted_index = int(np.argmax(probs))
        predicted_grade = self.grade_min + predicted_index

        grade_probabilities = {
            (self.grade_min + i): float(probs[i])
            for i in range(min(len(probs), (self.grade_max - self.grade_min + 1)))
        }
        confidence = grade_probabilities.get(predicted_grade, float(probs[predicted_index]))

        # 5) Price lookup (as-is from local snapshot)
        prices = self.price_lookup.get_prices(card_id, tcgplayer_id = tcgplayer_id)

        return {
            "status": "success",
            "card": {
                "card_id": card_id,
                "card_name": id_dict.get("card_name"),
                "set_name": id_dict.get("set_name"),
                "identification_confidence": float(id_dict.get("confidence", 0.0)),
                "identification_method": id_dict.get("method"),
                "identification_debug": id_dict.get("debug", {}),
            },
            "variants": {
                "is_first_edition": variants.get("is_first_edition"),
                "is_shadowless": variants.get("is_shadowless"),
                "is_holo": variants.get("is_holo"),
                "variant_method": variants.get("method"),
                "variant_debug": variants.get("debug", {}),
            },
            "condition_features": feats,
            "grade_prediction": {
                "predicted_grade": int(predicted_grade),
                "confidence": float(confidence),
                "all_probabilities": grade_probabilities,
                "grade_range": [self.grade_min, self.grade_max],
            },
            "prices": prices,
        }

    # --- custom objects for loading model
    def _ordinal_loss(self, y_true, y_pred):
        ce = tf.keras.losses.sparse_categorical_crossentropy(y_true, y_pred)
        y_true_grade = tf.cast(y_true, tf.int32)
        y_pred_grade = tf.cast(tf.argmax(y_pred, axis=1), tf.int32)
        distance = tf.abs(y_pred_grade - y_true_grade)
        return ce * (1.0 + tf.cast(distance, tf.float32) * 0.3)

    def _mae_grades(self, y_true, y_pred):
        y_true_grade = tf.cast(y_true, tf.float32)
        y_pred_grade = tf.cast(tf.argmax(y_pred, axis=1), tf.float32)
        return tf.reduce_mean(tf.abs(y_true_grade - y_pred_grade))

    def _within_1_acc(self, y_true, y_pred):
        y_true_grade = tf.cast(y_true, tf.int32)
        y_pred_grade = tf.cast(tf.argmax(y_pred, axis=1), tf.int32)
        within_1 = tf.abs(y_pred_grade - y_true_grade) <= 1
        return tf.reduce_mean(tf.cast(within_1, tf.float32))


if __name__ == "__main__":
    valuator = PokemonCardValuator()
    print(valuator.valuate("test_images/charizard.jpg"))
