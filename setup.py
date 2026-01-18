from setuptools import setup, find_packages

"""
Editable install configuration for pokemon-card-valuator.

This allows:
    pip install -e .

So imports like:
    from src.pokemon_valuator.pipeline.prediction_pipeline_final import PokemonCardValuator

work from anywhere.

NOTE:
- We intentionally package `src.*` because the repo uses `src/` as the
  top-level import namespace (not the typical src-layout).
"""

setup(
    name="pokemon-card-valuator",
    version="0.1.0",
    description="Pokemon Card Valuator (Identification + PSA Grading + Price Lookup)",
    author="Your Name",
    python_requires=">=3.10",
    packages=find_packages(include=["src", "src.*"]),
    include_package_data=True,
    install_requires=[
        # minimal runtime deps (full list still in requirements.txt)
        "numpy",
        "pandas",
        "pyyaml",
    ],
)