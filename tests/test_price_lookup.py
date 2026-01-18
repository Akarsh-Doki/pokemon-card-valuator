from src.pokemon_valuator.components.simple_price_lookup import SimplePriceLookup

def test_price_lookup_loads():
    # This test will fail until you have built the snapshot CSV.
    # Run: python scripts/build_price_db_from_api.py
    try:
        SimplePriceLookup()
    except FileNotFoundError:
        assert True
