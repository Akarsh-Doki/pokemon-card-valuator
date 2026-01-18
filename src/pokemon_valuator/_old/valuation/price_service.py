from typing import Optional
from pokemon_valuator.valuation.types import PriceResult
from pokemon_valuator.valuation.price_cache import PriceCacheCSV

class PriceService:
    def __init__(self, cache: PriceCacheCSV, api_client=None, enable_api: bool = True):
        self.cache = cache
        self.api = api_client
        self.enable_api = enable_api

    def get_price(self, card_id: str) -> Optional[PriceResult]:
        cached = self.cache.get(card_id)
        if cached:
            return cached

        if not self.enable_api or not self.api:
            return None

        pr = self.api.fetch_price(card_id)
        if pr:
            self.cache.upsert(pr)
        return pr