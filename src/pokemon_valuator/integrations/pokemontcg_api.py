from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class PokemonTCGClient:
    """
    Client for https://api.pokemontcg.io/v2
    """
    def __init__(
        self,
        base_url: str = "https://api.pokemontcg.io/v2",
        api_key: Optional[str] = None,
        timeout_sec: int = 8,
        cache_dir: Optional[str] = None,
        max_retries: int = 4,
        backoff_factor: float = 0.6,
        fail_soft: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = int(timeout_sec)
        self.fail_soft = bool(fail_soft)

        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.session = requests.Session()
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
            respect_retry_after_header=True
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _headers(self) -> Dict[str, str]:
        h = {
            "Accept": "application/json",
            "User-Agent": "pokemon-card-valuator/1.0",
        }
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    def _cache_key(self, path: str, params: Dict[str, Any]) -> Optional[Path]:
        if not self.cache_dir:
            return None
        raw = json.dumps({"base_url": self.base_url, "path": path, "params": params}, sort_keys=True).encode("utf-8")
        name = hashlib.md5(raw).hexdigest() + ".json"
        return self.cache_dir / name
    
    @staticmethod
    def _is_empty_payload(payload: Any) -> bool:
        try:
            return isinstance(payload, dict) and isinstance(payload.get("data"), list) and len(payload["data"]) == 0
        except Exception:
            return False
        
    def _request_once(self, base_url: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{base_url.rstrip('/')}{path}"
        try:
            r = self.session.get(url, params=params, headers=self._headers(), timeout=self.timeout_sec)
        except requests.RequestException as e:
            if self.fail_soft:
                return {
                    "data": [],
                    "page": params.get("page", 1),
                    "pageSize": params.get("pageSize", 0),
                    "count": 0,
                    "totalCount": 0,
                    "error": str(e),
                }
            raise

        if (self.fail_soft) and (r.status_code) in (403, 404, 451):
            return {"data": [], "page": params.get("page", 1), "pageSize": params.get("pageSize", 0), "count": 0, "totalCount": 0}

        if r.status_code >= 400:
            snippet = (r.text or "")[:400]
            raise RuntimeError(
                f"PokemonTCG API error {r.status_code}\n"
                f"URL: {r.url}\n"
                f"BODY: {snippet}"
            )

        ct = (r.headers.get("content-type") or "").lower()
        if "application/json" not in ct:
            if self.fail_soft:
                return {"data": [], "page": params.get("page", 1), "pageSize": params.get("pageSize", 0), "count": 0, "totalCount": 0}
            raise RuntimeError(f"Unexpected content-type: {ct}\nURL: {r.url}\nBODY: {(r.text or '')[:300]}")

        return r.json()

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        cache_fp = self._cache_key(path, params)
        if cache_fp and cache_fp.exists():
            try:
                cached = json.loads(cache_fp.read_text(encoding="utf-8"))
                if self._is_empty_payload(cached):
                    try:
                        cache_fp.unlink()
                    except Exception:
                        pass
                else:
                    return cached
            except Exception:
                pass

        data = None
        last_err = None

        try:
            data = self._request_once(self.base_url, path, params)
        except Exception as e:
            last_err = str(e)

        if data is None:
            for fb in ["https://api.pokemontcg.io/v2", "https://api.pokemontcg.io"]:
                try:
                    if fb.endswith("/v2"):
                        data = self._request_once(fb, path, params)
                    else:
                        data = self._request_once(fb, "/v2" + path, params)
                    break
                except Exception as e2:
                    last_err = str(e2)
                    continue

        if data is None:
            raise RuntimeError(last_err or "PokemonTCG API request failed")

        if cache_fp and not self._is_empty_payload(data):
            try:
                cache_fp.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass

        return data

    def search_cards(self, q: str, page_size: int = 20, page: int = 1) -> List[Dict[str, Any]]:
        j = self._get("/cards", {"q": q, "pageSize": int(page_size), "page": int(page)})
        return j.get("data") or []
    def get_card_by_id(self, card_id: str) -> dict | None:
        path = f"/cards/{card_id}"
        params: Dict[str, Any] = {}

        cache_fp = self._cache_key(path, params)
        if cache_fp and cache_fp.exists():
            try:
                data = json.loads(cache_fp.read_text(encoding="utf-8")) or {}
                cached_card = data.get("data")
                if cached_card is None:
                    try:
                        cache_fp.unlink()
                    except Exception:
                        pass
                else:
                    return cached_card
            except Exception:
                pass

        url = f"{self.base_url}{path}"
        try:
            r = self.session.get(url, headers=self._headers(), timeout=self.timeout_sec)

            if self.fail_soft and r.status_code in (403, 404, 451):
                return None

            if r.status_code >= 400:
                snippet = (r.text or "")[:400]
                raise RuntimeError(
                    f"PokemonTCG API error {r.status_code}\n"
                    f"URL: {r.url}\n"
                    f"BODY: {snippet}"
                )

            ct = (r.headers.get("content-type") or "").lower()
            if "application/json" not in ct:
                if self.fail_soft:
                    return None
                raise RuntimeError(f"Unexpected content-type: {ct}\nURL: {r.url}\nBODY: {(r.text or '')[:300]}")

            data = r.json() or {}
            card = data.get("data")

            if cache_fp and card is not None:
                try:
                    cache_fp.write_text(json.dumps(data), encoding="utf-8")
                except Exception:
                    pass

            return card

        except requests.RequestException as e:
            if self.fail_soft:
                return None
            raise