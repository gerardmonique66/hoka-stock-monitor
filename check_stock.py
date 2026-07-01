#!/usr/bin/env python3
"""
Hoka Speedgoat 7 stock monitor.

Runs on GitHub Actions on a schedule, checks a specific colourway + US size on
hoka.com, and sends a WhatsApp message (via the free CallMeBot service) the moment
it flips from unavailable -> available. A tiny state.json file (committed back to
the repo by the workflow) stops it from pinging you on every single run.

Two check modes:
  * JSON mode  (recommended, exact per-size stock) - used when PID + COLOR_CODE are set.
  * HTML mode  (approximate "colourway now listed" watch) - used otherwise. Handy while
                the black/white colour isn't on the PH site yet: it alerts as soon as
                the colour string appears on the page you point it at.

All configuration comes from environment variables so nothing sensitive lives in the code.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request

# ----------------------------- CONFIG (via env vars) -----------------------------
# Defaults below are pre-filled for the Men's Speedgoat 7 "Black / White" on the
# Philippines store. Override any of them with GitHub repo Variables if needed.

# NOTE: we use `os.environ.get("X") or DEFAULT` (not the get(key, default) form) so
# that a GitHub Variable that exists but is *empty* still falls back to the default.

# Link included in the alert (the men's SG7 product page on PH).
PRODUCT_URL = (os.environ.get("PRODUCT_URL") or
    "https://www.hoka.com/en/ph/men/speedgoat-7/1171928.html?dwvar_1171928_color=BWHT")

TARGET_COLOR = os.environ.get("TARGET_COLOR") or "black/white"   # for HTML fallback only
TARGET_SIZE = os.environ.get("TARGET_SIZE") or "11"              # US size

# --- JSON mode (exact per-size stock) — confirmed values for men's SG7 Black/White ---
#   PID 1171928 = men's Speedgoat 7  (women's is 1171929)
#   BWHT        = the Black / White colour code
# These work against the PH catalogue even while the colour is hidden from the
# category page, because the product record still exists.
PID = (os.environ.get("PID") or "1171928").strip()
COLOR_CODE = (os.environ.get("COLOR_CODE") or "BWHT").strip()
SITE_PATH = os.environ.get("SITE_PATH") or "Sites-HOKA-PH-Site/en_PH"  # PH storefront

# --- WhatsApp via CallMeBot (set as GitHub Secrets, never hard-code) ---
WA_PHONE = os.environ.get("WA_PHONE", "")     # your number incl. country code, e.g. +639171234567
WA_APIKEY = os.environ.get("WA_APIKEY", "")   # the key CallMeBot sends you

STATE_FILE = "state.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
# ---------------------------------------------------------------------------------


def http_get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",   # Demandware AJAX endpoints expect this
        "Referer": "https://www.hoka.com/",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "sec-ch-ua": '"Chromium";v="125", "Not.A/Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _norm(s):
    """Lowercase and squeeze spaces around slashes so 'black / white' == 'black/white'."""
    return re.sub(r"\s*/\s*", "/", s.lower())


def check_via_json():
    """Exact path: ask Demandware's Product-Variation endpoint about this size."""
    base = f"https://www.hoka.com/on/demandware.store/{SITE_PATH}/Product-Variation"
    params = {
        "pid": PID,
        f"dwvar_{PID}_color": COLOR_CODE,
        f"dwvar_{PID}_size": TARGET_SIZE,
        "quantity": "1",
    }
    url = base + "?" + urllib.parse.urlencode(params)
    data = json.loads(http_get(url))
    prod = data.get("product", {}) if isinstance(data, dict) else {}
    available = bool(prod.get("available"))
    msgs = " ".join(prod.get("availability", {}).get("messages", []) or []).lower()
    if "out of stock" in msgs or "sold out" in msgs:
        available = False
    return available, url


def check_via_html():
    """Approximate path: alert when the target colour string appears on the page."""
    page = _norm(http_get(PRODUCT_URL))
    target = _norm(TARGET_COLOR)
    colour_listed = target in page if target else False
    # If the colour is listed, make sure it isn't explicitly sold out on the page.
    sold_out = ("sold out - join the waitlist" in page) or ("out of stock" in page)
    return (colour_listed and not sold_out), PRODUCT_URL


def notify(text):
    if not (WA_PHONE and WA_APIKEY):
        print("WhatsApp not configured (WA_PHONE / WA_APIKEY missing). Would have sent:")
        print(text)
        return
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode({
        "phone": WA_PHONE, "text": text, "apikey": WA_APIKEY,
    })
    try:
        print("CallMeBot:", http_get(url)[:200])
    except Exception as e:
        print("Notify failed:", e)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"in_stock": False}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def main():
    was_in_stock = bool(load_state().get("in_stock"))

    try:
        if PID and COLOR_CODE:
            available, checked = check_via_json()
            mode = "json"
        else:
            available, checked = check_via_html()
            mode = "html(approx)"
    except Exception as e:
        # Transient error (block, timeout, layout change): don't flip state, just exit clean.
        print(f"Check failed: {e}. Leaving state unchanged.")
        return

    print(f"[{mode}] available={available}  size={TARGET_SIZE}  colour={TARGET_COLOR}")
    print(f"checked: {checked}")

    if available and not was_in_stock:
        msg = (f"IN STOCK: Hoka Speedgoat 7 {TARGET_COLOR} US {TARGET_SIZE}\n"
               f"{PRODUCT_URL}\n(Reminder: PH store needs a member login to buy.)")
        notify(msg)
        print("Notification sent.")
    elif available:
        print("Still in stock (already alerted). Staying quiet.")
    else:
        print("Not available yet.")

    save_state({"in_stock": available})


if __name__ == "__main__":
    main()
    sys.exit(0)
