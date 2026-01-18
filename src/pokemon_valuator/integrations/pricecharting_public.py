from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from html import unescape


PRICECHARTING_BASE = "https://www.pricecharting.com"
SEARCH_URL = f"{PRICECHARTING_BASE}/search-products"

@dataclass
class PriceChartingResult:
    """
    Safe to construct with no args (so CardIdentifier can store a placeholder).
    """
    url: str = ""
    title: Optional[str] = None

    status: str = "missing_metadata"
    message: Optional[str] = None
    debug: Dict[str, Any] = field(default_factory=dict)

def _ua_headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

def _money_to_float(s: str) -> Optional[float]:
    if not s:
        return None
    t = s.strip().replace(",", "")
    t = re.sub(r"[^0-9.]", "", t)
    if not t:
        return None
    try:
        return float(t)
    except Exception:
        return None

def _normalize_grade_keys(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": d.get("status"),
        "url": d.get("url"),
        "ungraded": d.get("ungraded"),
        "psa7": d.get("grade7"),
        "psa8": d.get("psa8"),
        "psa9": d.get("psa9"),
        "psa95": d.get("grade95"),
        "psa10": d.get("psa10"),
        "source_row": d.get("source_row"),
    }

def _normalize_query(card_name: str, set_name: str, card_number: str) -> List[str]:
    card_name = (card_name or "").strip()
    set_name = (set_name or "").strip()
    num_only = (card_number or "").split("/")[0].strip()

    base = " ".join([p for p in [card_name, num_only, set_name] if p]).strip()

    queries = [
        base,
        f"{card_name} #{num_only} {set_name}".strip(),
        f"{card_name} {set_name} {num_only}".strip(),
        f"{card_name} {num_only}".strip(),
        f"{card_name} {set_name}".strip(),
        f"pokemon tcg {card_name} {num_only} {set_name}".strip(),
        f"pokemon {card_name} {num_only}".strip(),
    ]

    out: List[str] = []
    seen = set()
    for q in queries:
        q2 = re.sub(r"\s+", " ", q).strip()
        if q2 and q2.lower() not in seen:
            seen.add(q2.lower())
            out.append(q2)
    return out

def _is_bad_url(url: str) -> bool:
    if not url:
        return True
    if "*" in url:
        return True
    if url.rstrip("/").endswith("/game") or url.rstrip("/").endswith("/product"):
        return True
    return False

def _extract_title_from_html(html: str) -> Optional[str]:
    if not html:
        return None
    m = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    t = m.group(1)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*\|\s*PriceCharting.*$", "", t, flags=re.IGNORECASE).strip()
    return t or None

def _looks_like_pokemon_tcg_page(url: str, html: str) -> bool:
    u = (url or "").lower()
    h = (html or "").lower()

    if "pokemon" in u and ("tcg" in u or "trading-card" in u or "card" in u):
        return True

    if "pokemon" in h and ("tcg" in h or "trading card" in h or "psa 10" in h):
        return True

    return False

def _slugify_pricecharting(s: str) -> str:
    if not s:
        return ""
    t = s.strip().lower()

    t = t.replace("’", "'")
    t = t.replace("é", "e")
    t = re.sub(r"[^a-z0-9\s\-']", " ", t)
    t = t.replace("'", "")
    t = re.sub(r"\s+", " ", t).strip()
    t = t.replace(" ", "-")
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t

def _try_fetch_title(url: str, timeout: int = 20) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout, headers=_ua_headers())
        if r.status_code >= 400:
            return None
        m = re.search(r"<title>(.*?)</title>", r.text or "", flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        return title or None
    except Exception:
        return None


def _looks_like_real_game_page(html: str) -> bool:
    if not html:
        return False
    h = html.lower()
    return ("compare vs other items" in h) or ("full price guide" in h) or ("price guide" in h)

def _verify_pricecharting_url(url: str, timeout: int = 20) -> bool:
    if _is_bad_url(url):
        return False
    try:
        r = requests.get(url, timeout=timeout, headers=_ua_headers())
        if r.status_code >= 400:
            return False
        html = r.text or ""
        if "/search-products" in (r.url or ""):
            return False
        return _looks_like_real_game_page(html)
    except Exception:
        return False


def search_pricecharting_page(
    *,
    card_name: str,
    set_name: str,
    card_number: str,
    timeout: int = 20,
) -> Optional[PriceChartingResult]:
    if not card_name or not set_name or not card_number:
        return None

    num_only = (card_number or "").split("/")[0].strip()
    if not num_only or not num_only.isdigit():
        return None

    queries = _normalize_query(card_name, set_name, card_number)
    link_re = re.compile(
        r"""href\s*=\s*["'](https?://www\.pricecharting\.com)?(/(?:game|product)/[^"'>\s]+)["']""",
        re.IGNORECASE,
    )

    for q in queries:
        params = {"type": "prices", "q": q}
        try:
            r = requests.get(
                SEARCH_URL,
                params=params,
                timeout=timeout,
                headers=_ua_headers(),
            )
        except Exception:
            continue

        if r.status_code >= 400:
            continue

        html = r.text or ""
        hits = link_re.findall(html)

        links: List[str] = []
        for _abs, path in hits:
            if path:
                links.append(path)

        if not links:
            m = re.search(r'(/(?:game|product)/[^"\'\s>]+)', html, flags=re.IGNORECASE)
            if m:
                links = [m.group(1)]

        if not links:
            continue
        game_links = [lnk for lnk in links if lnk.lower().startswith("/game/")]
        chosen = game_links[0] if game_links else links[0]

        url = PRICECHARTING_BASE + chosen
        if _is_bad_url(url):
            continue

        title = _try_fetch_title(url, timeout=timeout)
        return PriceChartingResult(url=url, title=title, status="resolved")

    set_slug = _slugify_pricecharting(set_name)
    card_slug = _slugify_pricecharting(card_name)

    guessed = f"{PRICECHARTING_BASE}/game/pokemon-{set_slug}/{card_slug}-{int(num_only)}"
    if _verify_pricecharting_url(guessed, timeout=timeout):
        title = _try_fetch_title(guessed, timeout=timeout)
        return PriceChartingResult(url=guessed, title=title)

    return None

def search_pricecharting_pages(card_name: str, set_name: str, card_number: str, limit: int = 6):
    if not card_name or not set_name or not card_number:
        return []

    queries = _normalize_query(card_name, set_name, card_number)

    left_num = (card_number or "").split("/")[0].strip()
    set_slug = _slugify_pricecharting(set_name).lower()
    name_slug = _slugify_pricecharting(card_name).lower()

    seen = set()
    candidates = []
    link_re = re.compile(
        r"""href\s*=\s*["'](https?://www\.pricecharting\.com)?(/(?:game|product)/[^"' >]+)["']""",
        re.IGNORECASE,
    )

    for q in queries:
        try:
            r = requests.get(
                SEARCH_URL,
                params={"type": "prices", "q": q},
                timeout=12,
                headers=_ua_headers(),
            )
        except Exception:
            continue

        if r.status_code >= 400:
            continue

        html = r.text or ""
        if not html:
            continue

        hits = link_re.findall(html)
        if not hits:
            # extra-loose fallback
            m = re.search(r'(/(?:game|product)/[^"\'\s>]+)', html, flags=re.IGNORECASE)
            hits = [("", m.group(1))] if m else []

        for _abs, path in hits:
            if not path:
                continue
            full = PRICECHARTING_BASE + path

            if _is_bad_url(full):
                continue
            if full in seen:
                continue

            seen.add(full)
            candidates.append((None, full))
        if len(candidates) >= max(1, int(limit)) * 3:
            break

    if not candidates:
        return []

    def score(item):
        _title, full = item
        u = (full or "").lower()
        s = 0

        if left_num and left_num in u:
            s += 5

        if set_slug and set_slug in u:
            s += 3
        if name_slug and name_slug in u:
            s += 2

        if "pokemon" in u:
            s += 2

        if "/game/" in u:
            s += 1

        return s
    
    filtered = []
    for t, full in candidates:
        u = (full or "").lower()
        if set_slug and f"/game/pokemon-{set_slug}/" not in u:
            continue
        if name_slug and name_slug not in u:
            continue
        filtered.append((t, full))

    candidates = filtered or candidates

    candidates.sort(key=score, reverse=True)

    out = []
    for _title, full in candidates[: max(1, int(limit))]:
        real_title = _try_fetch_title(full, timeout=12) or _title
        out.append(PriceChartingResult(url=full, title=real_title, status="resolved"))
    return out

def _strip_tags_keep_signs(html: str) -> str:
    if not html:
        return ""
    s = html.replace("&nbsp;", " ")
    s = re.sub(r"<script\b.*?</script>", " ", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<style\b.*?</style>", " ", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _parse_compare_vs_other_items(html: str) -> Dict[str, Any]:
    if not html:
        return {"status": "not_found"}

    m = re.search(r"Compare\s+vs\s+Other\s+Items", html, flags=re.IGNORECASE)
    if not m:
        return {"status": "not_found"}

    region = html[m.start(): m.start() + 40000]

    tm = re.search(r"<table\b.*?</table>", region, flags=re.IGNORECASE | re.DOTALL)
    if not tm:
        return {"status": "not_found"}

    table_html = tm.group(0)
    rows = re.findall(r"<tr\b.*?</tr>", table_html, flags=re.IGNORECASE | re.DOTALL)
    if not rows:
        return {"status": "not_found"}

    def strip_tags(s: str) -> str:
        s = s.replace("&nbsp;", " ")
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s
    unsigned_money_re = re.compile(r"(?<![+\-])\$([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)")
    any_money_or_dash_re = re.compile(r"(\$[0-9]|-)", re.IGNORECASE)

    header_idx = None
    for i, row in enumerate(rows):
        cells = re.findall(r"<t[hd]\b.*?</t[hd]>", row, flags=re.IGNORECASE | re.DOTALL)
        texts = [strip_tags(c).lower() for c in cells]
        joined = " | ".join(texts)

        if (
            "ungraded" in joined
            and "grade 7" in joined
            and "grade 8" in joined
            and "grade 9" in joined
            and ("grade 9.5" in joined or "grade 9.5" in joined.replace(" ", ""))
            and "psa 10" in joined
        ):
            header_idx = i
            break

    if header_idx is None:
        return {"status": "not_found"}

    data_row = None
    for j in range(header_idx + 1, min(header_idx + 6, len(rows))):
        r = rows[j]
        tds = re.findall(r"<td\b.*?</td>", r, flags=re.IGNORECASE | re.DOTALL)
        if len(tds) >= 6 and any_money_or_dash_re.search(r):
            data_row = r
            break

    if data_row is None:
        return {"status": "not_found"}

    data_cells = re.findall(r"<td\b.*?</td>", data_row, flags=re.IGNORECASE | re.DOTALL)
    if len(data_cells) < 6:
        return {"status": "not_found"}

    vals: List[Optional[float]] = []

    for td in data_cells[:6]:
        mt = unsigned_money_re.search(td)
        if mt:
            v = _money_to_float("$" + mt.group(1))
            vals.append(v)
        else:
            # if cell has '-' or only signed deltas, treat as missing
            vals.append(None)

    return {
        "status": "success",
        "ungraded": vals[0],
        "grade7": vals[1],
        "psa8": vals[2],
        "psa9": vals[3],
        "grade95": vals[4],
        "psa10": vals[5],
    }

def _parse_compare_vs_other_items_textblock(html: str) -> Dict[str, Any]:
    if not html:
        return {"status": "not_found"}

    m = re.search(r"Compare\s+vs\s+Other\s+Items", html, flags=re.IGNORECASE)
    if not m:
        return {"status": "not_found"}

    # Take a big region: sometimes the relevant label/price text appears after the table.
    region = html[m.start(): m.start() + 70000]
    if not region:
        return {"status": "not_found"}
    txt = region.replace("&nbsp;", " ")
    txt = re.sub(r"<script\b.*?</script>", " ", txt, flags=re.IGNORECASE | re.DOTALL)
    txt = re.sub(r"<style\b.*?</style>", " ", txt, flags=re.IGNORECASE | re.DOTALL)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()

    if not txt:
        return {"status": "not_found"}

    def find_price(label_regex: str) -> Optional[float]:
        mm = re.search(
            rf"{label_regex}\s*\$([0-9]{{1,3}}(?:,[0-9]{{3}})*(?:\.[0-9]{{1,2}})?)",
            txt,
            flags=re.IGNORECASE,
        )
        if not mm:
            return None
        return _money_to_float("$" + mm.group(1))

    ungraded = find_price(r"Ungraded")
    grade7 = find_price(r"Grade\s*7")
    psa8 = find_price(r"(?:Grade\s*8|PSA\s*8)")
    psa9 = find_price(r"(?:Grade\s*9(?!\.)|PSA\s*9)")
    grade95 = find_price(r"Grade\s*9\.?\s*5")
    psa10 = find_price(r"(?:PSA\s*10|Grade\s*10)")
    if all(v is None for v in (ungraded, grade7, psa8, psa9, grade95, psa10)):
        return {"status": "not_found"}

    return {
        "status": "success",
        "ungraded": ungraded,
        "grade7": grade7,
        "psa8": psa8,
        "psa9": psa9,
        "grade95": grade95,
        "psa10": psa10,
        "source_row": "compare_vs_other_items_textblock",
    }

def scrape_pricecharting_psa_prices(
    *,
    url: str,
    timeout: int = 20,
    sleep_sec: float = 0.0,
) -> Dict[str, Any]:
    if _is_bad_url(url):
        return {
            "status": "missing_metadata",
            "source": "pricecharting_public",
            "message": "Resolver failed (bad url).",
            "url": url,
        }

    if sleep_sec > 0:
        time.sleep(sleep_sec)

    try:
        r = requests.get(url, timeout=timeout, headers=_ua_headers())
    except Exception as e:
        return {"status": "http_error", "url": url, "message": str(e)}

    if r.status_code >= 400:
        return {"status": "http_error", "url": url, "http_status": r.status_code}

    html = r.text or ""
    if not html:
        return {"status": "parse_failed", "url": url, "message": "empty html"}
    cmp = _parse_compare_vs_other_items(html)
    if cmp.get("status") == "success":
        return _normalize_grade_keys({
            "status": "success",
            "url": url,
            "ungraded": cmp.get("ungraded"),
            "grade7": cmp.get("grade7"),
            "psa8": cmp.get("psa8"),
            "psa9": cmp.get("psa9"),
            "grade95": cmp.get("grade95"),
            "psa10": cmp.get("psa10"),
            "source_row": "compare_vs_other_items_table",
        })
    cmp_txt = _parse_compare_vs_other_items_textblock(html)
    if cmp_txt.get("status") == "success":
        return _normalize_grade_keys({
            "status": "success",
            "url": url,
            "ungraded": cmp_txt.get("ungraded"),
            "grade7": cmp_txt.get("grade7"),
            "psa8": cmp_txt.get("psa8"),
            "psa9": cmp_txt.get("psa9"),
            "grade95": cmp_txt.get("grade95"),
            "psa10": cmp_txt.get("psa10"),
            "source_row": cmp_txt.get("source_row") or "compare_vs_other_items_textblock",
        })

    def find_price_for_labels(labels: List[str]) -> Optional[float]:
        for lab in labels:
            pat = re.compile(
                rf"{lab}.{{0,200}}(\$[0-9,]+(?:\.[0-9]{{2}})?)",
                re.IGNORECASE,
            )
            mm = pat.search(html)
            if mm:
                return _money_to_float(mm.group(1))
        return None

    ungraded = find_price_for_labels(["Ungraded"])
    psa8 = find_price_for_labels([r"Grade\s*8", r"PSA\s*8"])
    psa9 = find_price_for_labels([r"Grade\s*9(?!\.)", r"PSA\s*9"])
    psa10 = find_price_for_labels([r"PSA\s*10", r"Grade\s*10"])

    if ungraded is None and psa8 is None and psa9 is None and psa10 is None:
        return {"status": "not_found", "url": url}
    return _normalize_grade_keys({
        "status": "success",
        "url": url,
        "ungraded": ungraded,
        "grade7": None,
        "psa8": psa8,
        "psa9": psa9,
        "grade95": None,
        "psa10": psa10,
        "source_row": "fallback_regex",
    })


import json
import re
from datetime import datetime

def _extract_balanced(text: str, start_idx: int, open_ch: str, close_ch: str) -> str:
    depth = 0
    out = []
    for i in range(start_idx, len(text)):
        ch = text[i]
        out.append(ch)
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return "".join(out)
    return ""

def _js_object_to_json(s: str) -> str:
    # Quote unquoted keys:  name: -> "name":
    s = re.sub(r"(\w+)\s*:", r'"\1":', s)
    # Convert single quotes to double quotes
    s = s.replace("'", '"')
    # Remove trailing commas
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return s

def fetch_price_history(variant_url: str) -> dict:
    """
    Returns history series for a PriceCharting variant page.

    Output format:
    {
      "source": "pricecharting_public",
      "series": [
        {"name": "Ungraded", "points": [{"date":"YYYY-MM-DD","price": 1.23}, ...]},
        {"name": "PSA 10", "points": [...]},
        ...
      ]
    }

    Strategy:
      1) Try Highcharts scrape (rarely present)
      2) Fallback: scrape Sold Listings table (works for most cards)
    """
    if not variant_url or _is_bad_url(variant_url):
        return {"source": "pricecharting_public", "series": []}

    html = ""
    out: List[dict] = []

    try:
        r = requests.get(variant_url, timeout=20, headers=_ua_headers())
        if r.status_code >= 400:
            return {"source": "pricecharting_public", "series": []}

        html = r.text or ""

        # -----------------------
        # Attempt #1: Highcharts
        # -----------------------
        if "Highcharts" in html and "series" in html:
            m = re.search(
                r"series\\s*:\\s*(\\[[\\s\\S]*?\\])\\s*,\\s*(?:rangeSelector|navigator|tooltip)",
                html,
            )
            if m:
                raw = m.group(1)

                # JSON-ish → more parseable
                safe = raw.replace("'", '"')

                # Remove JS functions inside (Highcharts sometimes has them)
                safe = re.sub(r"function\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\}", "null", safe)

                try:
                    series_obj = json.loads(safe)
                except Exception:
                    series_obj = []

                for s in series_obj if isinstance(series_obj, list) else []:
                    name = s.get("name") or "Series"
                    data = s.get("data") or []
                    pts = []

                    for item in data:
                        # Stock format: [timestamp_ms, price]
                        if (
                            isinstance(item, list)
                            and len(item) >= 2
                            and isinstance(item[0], (int, float))
                            and isinstance(item[1], (int, float))
                        ):
                            ts_ms = int(item[0])
                            price = float(item[1])
                            dt = datetime.utcfromtimestamp(ts_ms / 1000.0).strftime("%Y-%m-%d")
                            pts.append({"date": dt, "price": price})

                    if pts:
                        out.append({"name": name, "points": pts})

    except Exception:
        out = []

    # -----------------------------------------
    # Attempt #2: Sold Listings fallback (BEST)
    # -----------------------------------------
    if not out and html:
        out = _build_series_from_sold_listings_html(html)

    return {"source": "pricecharting_public", "series": out}

def _clean_text(s: Any) -> str:
    if s is None:
        return ""
    s = unescape(str(s))
    s = s.replace("\xa0", " ")
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _parse_sale_date(raw: str) -> str:
    """
    PriceCharting usually uses YYYY-MM-DD, but be defensive.
    Returns YYYY-MM-DD or "".
    """
    raw = _clean_text(raw)
    if not raw:
        return ""

    # 2026-01-16
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)

    # Jan 16, 2026
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass

    return ""

def _parse_pc_date(s: str) -> Optional[str]:
    """
    PriceCharting Sold Listings often contains:
      - YYYY-MM-DD
      - MM/DD/YY or MM/DD/YYYY
      - Jan 01, 2025

    Returns normalized YYYY-MM-DD or None
    """
    s = _clean_text(s)

    # YYYY-MM-DD
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return s

    # MM/DD/YY or MM/DD/YYYY
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        mm = int(m.group(1))
        dd = int(m.group(2))
        yy = int(m.group(3))
        if yy < 100:
            yy += 2000
        try:
            return datetime(yy, mm, dd).strftime("%Y-%m-%d")
        except Exception:
            return None

    # "Jan 01, 2025"
    try:
        return datetime.strptime(s, "%b %d, %Y").strftime("%Y-%m-%d")
    except Exception:
        return None


def _build_series_from_sold_listings_html(html: str) -> list:
    """
    Scrapes the Sold Listings table and builds multiple history series.

    Works for basically ALL PriceCharting pages because the sold table is always present.
    Builds daily averages per grade bucket (Ungraded, PSA 7/8/9/9.5/10, BGS/CGC if present).
    """
    try:
        soup = BeautifulSoup(html, "lxml")

        def grade_from_title(title: str) -> str:
            t = (title or "").upper()

            # PSA grades
            m = re.search(r"PSA\s*(10|9\.5|9|8|7)", t)
            if m:
                g = m.group(1)
                return "PSA 9.5" if g == "9.5" else f"PSA {g}"

            # BGS grades
            m2 = re.search(r"BGS\s*(10|9\.5|9|8|7)", t)
            if m2:
                g = m2.group(1)
                return f"BGS {g}"

            # CGC grades
            m3 = re.search(r"CGC\s*(10|9\.5|9|8|7)", t)
            if m3:
                g = m3.group(1)
                return f"CGC {g}"

            return "Ungraded"

        # ------------------------------------------------------------
        # 1) Find the Sold Listings table (robust header matching)
        # ------------------------------------------------------------
        table = None
        for t in soup.find_all("table"):
            header_text = " ".join(th.get_text(" ", strip=True) for th in t.find_all("th"))
            header_text = _clean_text(header_text)

            # PriceCharting headers often include arrows/sorting text,
            # so we check for substrings instead of exact equals
            if ("Sale Date" in header_text) and ("Price" in header_text) and ("Title" in header_text):
                table = t
                break

        if table is None:
            return []

        # ------------------------------------------------------------
        # 2) Parse rows with flexible column positions
        # ------------------------------------------------------------
        # series_name -> date -> list[prices]
        buckets: Dict[str, Dict[str, List[float]]] = {}

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue

            row_text = _clean_text(tr.get_text(" ", strip=True))
            if not row_text:
                continue

            # Date (look anywhere in the row)
            mdate = re.search(r"\d{4}-\d{2}-\d{2}", row_text)
            date_txt = _parse_sale_date(mdate.group(0) if mdate else "")
            if not date_txt:
                continue

            # Price (look anywhere in the row)
            mprice = re.search(r"\$\s*([0-9,]+(?:\.[0-9]+)?)", row_text)
            if not mprice:
                continue
            price = float(mprice.group(1).replace(",", ""))

            # Title: usually the longest TD, but just use the entire row text for grade detection
            series_name = grade_from_title(row_text)

            buckets.setdefault(series_name, {}).setdefault(date_txt, []).append(price)

        # ------------------------------------------------------------
        # 3) Convert to daily average points
        # ------------------------------------------------------------
        out = []
        for name, by_date in buckets.items():
            pts = []
            for d in sorted(by_date.keys()):
                vals = by_date[d]
                if not vals:
                    continue
                pts.append({"date": d, "price": float(sum(vals) / len(vals))})

            if pts:
                out.append({"name": name, "points": pts})

        # Stable ordering
        order = {
            "Ungraded": 0,
            "PSA 7": 1,
            "PSA 8": 2,
            "PSA 9": 3,
            "PSA 9.5": 4,
            "PSA 10": 5,
        }
        out.sort(key=lambda s: order.get(str(s.get("name")), 999))
        return out

    except Exception:
        return []



from datetime import datetime, timezone, timedelta

def scrape_pricecharting_sold_history(url: str, days: int = 30) -> Dict[str, Any]:
    """
    Scrapes PriceCharting public 'Sold Listings' table (no API key)
    and returns price points suitable for charting.

    Returns:
      {
        "status": "ok" | "error",
        "source_url": "...",
        "days": 30,
        "points": [{"ts": 1700000000, "date": "2026-01-01", "price": 12.34}, ...],
        "stats": {"min":..., "max":..., "avg":..., "latest":...}
      }
    """
    try:
        import requests
        from bs4 import BeautifulSoup

        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return {"status": "error", "message": f"HTTP {r.status_code}", "source_url": url, "days": days, "points": []}

        soup = BeautifulSoup(r.text, "lxml")

        # Look for the Sold Listings table (PriceCharting commonly uses a table with dates/prices)
        # We'll scan all tables and pick rows that look like: DATE | PRICE
        tables = soup.find_all("table")
        raw_points: List[Dict[str, Any]] = []

        def _parse_money(x: str) -> Optional[float]:
            if not x:
                return None
            x = x.strip().replace("$", "").replace(",", "")
            try:
                return float(x)
            except Exception:
                return None

        def _parse_date(x: str) -> Optional[datetime]:
            """
            PriceCharting dates are usually like 'Jan 3, 2026' or '1/3/26'
            We'll try multiple formats.
            """
            x = (x or "").strip()
            if not x:
                return None
            fmts = [
                "%b %d, %Y",   # Jan 03, 2026
                "%B %d, %Y",   # January 03, 2026
                "%m/%d/%y",    # 01/03/26
                "%m/%d/%Y",    # 01/03/2026
            ]
            for f in fmts:
                try:
                    return datetime.strptime(x, f).replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            return None

        for t in tables:
            rows = t.find_all("tr")
            for tr in rows:
                cols = tr.find_all(["td", "th"])
                if len(cols) < 2:
                    continue
                c0 = cols[0].get_text(" ", strip=True)
                c1 = cols[1].get_text(" ", strip=True)

                dt = _parse_date(c0)
                price = _parse_money(c1)

                if dt and price is not None:
                    raw_points.append(
                        {
                            "ts": int(dt.timestamp()),
                            "date": dt.date().isoformat(),
                            "price": float(price),
                        }
                    )

        if not raw_points:
            return {
                "status": "error",
                "message": "No sold listing rows found on page.",
                "source_url": url,
                "days": days,
                "points": [],
            }

        # Filter by last N days
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
        points = [p for p in raw_points if datetime.fromtimestamp(p["ts"], tz=timezone.utc) >= cutoff]

        # If filtering removed everything, keep last 30 raw points so the chart is never empty
        if not points:
            points = sorted(raw_points, key=lambda x: x["ts"], reverse=True)[:30]
            points = sorted(points, key=lambda x: x["ts"])

        # Compute stats
        prices = [p["price"] for p in points if isinstance(p.get("price"), (int, float))]
        stats = {}
        if prices:
            stats = {
                "min": float(min(prices)),
                "max": float(max(prices)),
                "avg": float(sum(prices) / max(1, len(prices))),
                "latest": float(prices[-1]),
            }

        return {
            "status": "ok",
            "source_url": url,
            "days": int(days),
            "points": points,
            "stats": stats,
        }

    except Exception as e:
        return {"status": "error", "message": str(e), "source_url": url, "days": days, "points": []}
