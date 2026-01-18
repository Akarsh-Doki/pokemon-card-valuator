from pokemon_valuator.models.card_identifier import CardIdentifier
from html import unescape 

ci = CardIdentifier(
    cards_reference_csv="data/raw/pokemon_tcg_api/cards_reference.csv",
    reference_images_dir="data/raw/pokemon_tcg_api/reference_images",
    yolo_weights_path="runs/yolo_regions/regions2/weights/best.pt",
)

imgs = [
    "test_images/IMG_1525.jpeg",
    "test_images/IMG_1526.jpeg",
    "test_images/IMG_1527.jpeg"
]

def _pick_ladder(p: dict) -> dict:
    if not isinstance(p, dict):
        return {}

    return {
        "ungraded": p.get("ungraded") or p.get("raw") or p.get("loose"),
        "psa7": p.get("psa7") or p.get("grade7"),
        "psa8": p.get("psa8") or p.get("grade8"),
        "psa9": p.get("psa9") or p.get("grade9"),
        "psa95": p.get("psa95") or p.get("grade95") or p.get("psa9.5") or p.get("grade9_5"),
        "psa10": p.get("psa10") or p.get("grade10"),
    }

def _pretty_print_block(title: str, block: dict, indent: str = "  "):
    if not block:
        print(f"{indent}{title}: None")
        return
    print(f"{indent}{title}:")
    for k in sorted(block.keys()):
        print(f"{indent}  {k}: {block.get(k)}")

for img in imgs:
    res = ci.identify(img)

    pc = (res.debug.get("pricecharting") or {})
    tcg = ((res.debug.get("pricing") or {}).get("tcgdex") or {})

    print("\n==", img, "==")
    print("STATUS:", res.status, "| CONF:", res.confidence, "| METHOD:", res.method)
    print("NAME:", res.card_name, "| SET:", res.set_name, "| NUM:", res.card_number)
    print("OCR set_code:", ((res.debug.get("ocr") or {}).get("set_code")))
    print("SETCODE_NARROWING:", ((res.debug.get("api") or {}).get("setcode_narrowing")))
    print("PRICECHARTING:", pc.get("status"), "variants:", len(pc.get("variants") or []))

    for i, v in enumerate(pc.get("variants") or [], start=1):
        p = (v.get("prices") or {})
        ladder = _pick_ladder(p)

        title = unescape(v.get("title") or "")
        url = v.get("url")

        print(f"  [{i}] {title}" if title else f"  [{i}]")
        print("      url:", url)
        print("      prices_status:", p.get("status"))
        print("      ladder:", ladder)


    print("TCGDEX:", tcg.get("status"))

    if tcg.get("status") == "success":
        detail = tcg.get("detail") or {}
        pricing = detail.get("pricing") or {}

        print("  TCGDEX pricing keys:", list(pricing.keys()))

        # USD-first “best view”
        preferred = pricing.get("preferred")
        cm_usd = pricing.get("cardmarket_usd")

        if preferred:
            _pretty_print_block("TCGDEX preferred (USD-first)", preferred, indent="  ")
        elif cm_usd:
            _pretty_print_block("TCGDEX cardmarket_usd", cm_usd, indent="  ")
        else:
            print("  TCGDEX USD view: (missing)  <-- your tcgdex prune didn't add conversion here")

        cm = pricing.get("cardmarket")
        tp = pricing.get("tcgplayer")

        _pretty_print_block("TCGDEX cardmarket (original)", cm, indent="  ")
        _pretty_print_block("TCGDEX tcgplayer (original)", tp, indent="  ")
