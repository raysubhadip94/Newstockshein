#!/usr/bin/env python3
"""
SHEIN Men's Category Product Monitor

Run on PC/laptop (not Termux) — requires curl_cffi:
    pip install requests curl_cffi

Default:  Monitors every 2s for NEW or RESTOCKED products → PDP stock check → alert if in stock

Manual Scan Commands:
  /check_existing   → scan ALL products (no filter)
  /chex <sizes>     → scan with size filter e.g. /chex M L 38

Monitor Filter Commands (persist until removed):
  /filter <sizes>   → only alert for NEW/RESTOCK that have these sizes e.g. /filter M L
  /filterout <sizes>→ suppress alerts for products with these sizes e.g. /filterout 38
  /rfilter          → remove all /filter sizes
  /rfilterout       → remove all /filterout sizes
  /chstat           → show current filter status

Other:
  /help             → full command guide
"""

import requests
import json
import time
import sys
import threading
import random
import socket
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.connection import create_connection as _orig_create_connection

try:
    from curl_cffi import requests as cffi_requests
    USE_CFFI = True
    print("✅ curl_cffi available — PDP requests will use Chrome TLS fingerprint")
except ImportError:
    USE_CFFI = False
    print("⚠️  curl_cffi not installed. Run: pip install curl_cffi")
    print("    PDP requests may get 403 without it.")


# ── IPv4 / IPv6 TRANSPORT ──────────────────────────────────────────────────────
# All listing + Telegram calls → IPv6 (default OS preference if available)
# PDP calls → force IPv4 to avoid Akamai blocks on IPv6

class IPv4HTTPAdapter(HTTPAdapter):
    """Forces all connections through IPv4 (AF_INET)."""
    def send(self, request, *args, **kwargs):
        # Temporarily monkey-patch socket.getaddrinfo to return only IPv4
        _orig = socket.getaddrinfo
        def _ipv4_only(host, port, family=0, *a, **kw):
            return _orig(host, port, socket.AF_INET, *a, **kw)
        socket.getaddrinfo = _ipv4_only
        try:
            return super().send(request, *args, **kwargs)
        finally:
            socket.getaddrinfo = _orig

def make_ipv4_session() -> requests.Session:
    """Returns a requests.Session that always connects over IPv4."""
    s = requests.Session()
    adapter = IPv4HTTPAdapter()
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

def make_ipv6_session() -> requests.Session:
    """Returns a plain requests.Session — uses OS default (prefers IPv6 if available)."""
    return requests.Session()

# Global IPv6 session for listing + Telegram (shared, thread-safe for reads)
_ipv6_session = make_ipv6_session()

# Thread-local storage — each PDP thread gets its own persistent curl_cffi session (IPv4)
_thread_local = threading.local()

def get_cffi_session():
    """
    Get or create a persistent curl_cffi session for the current thread.
    curl_options={CurlOpt.IPRESOLVE: 1} is set at Session() creation time —
    that's the correct API in 0.14.x (it's a Session-level param, not per-request).
    CURL_IPRESOLVE_V4 = 1 forces all connections to use IPv4 only.
    """
    if not hasattr(_thread_local, "session"):
        if USE_CFFI:
            from curl_cffi.curl import CurlOpt
            _thread_local.session = cffi_requests.Session(
                impersonate="chrome110",
                curl_options={CurlOpt.IPRESOLVE: 1},   # IPv4 only
            )
        else:
            _thread_local.session = make_ipv4_session()
    return _thread_local.session


def pdp_get_ipv4(session, url, **kwargs):
    """Thin wrapper — IPv4 is already baked into the session at creation time."""
    return session.get(url, **kwargs)

# ── CONFIG ─────────────────────────────────────────────────────────────────────
BOT_TOKEN      = "8679800339:AAH1uKwHey2l7tCyl3GPbJW0wzwCzS81I4w"
CHAT_ID        = "1276512925"
CHECK_INTERVAL = 2
BASE_URL       = "https://sheinindia.in"
STATE_FILE     = "shein_state.json"  # saves seen_snapshots + absent_codes between restarts

# ── LISTING API ─────────────────────────────────────────────────────────────────
LISTING_URL = (
    "https://search-edge.services.sheinindia.in/rilfnlwebservices/v4/rilfnl/products/"
    "category/sverse-5939-37961"
)
LISTING_PARAMS = {
    "advfilter": "true", "urgencyDriverEnabled": "true",
    "query": ":relevance:genderfilter:Men", "pageSize": 24,
    "store": "shein", "fields": "FULL", "currentPage": 0,
    "SearchExp1": "algo1", "SearchExp3": "suggester",
    "RelExp3": "false", "softFilters": "false", "stemFlag": "false",
    "SearchFlag5": "false", "SearchFlag6": "false",
    "platform": "android", "offer_price_ab_enabled": "false", "tagV2Enabled": "false",
}

# ── PDP API ─────────────────────────────────────────────────────────────────────
PDP_BASE   = "https://pdpaggregator-edge.services.sheinindia.in/aggregator/pdp"
PDP_PARAMS = {
    "storeId": "shein", "sortOptionsByColor": "true",
    "client_type": "Android/32", "client_version": "1.0.14",
    "isNewUser": "true", "pincode": "110001",
    "tagVersionTwo": "false", "applyExperiment ": "false", "fields": "FULL",
}

# ── HEADERS ─────────────────────────────────────────────────────────────────────
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Android",
    "Client_type": "Android/32",
    "Client_version": "1.0.14",
    "X-Tenant-Id": "SHEIN",
    "Ad_id": "342f47d0-910f-4a29-9bd7-cadb98a2eca9",
    "Accept-Encoding": "gzip, deflate, br",
}

# ── SHARED STATE ─────────────────────────────────────────────────────────────────
check_existing_lock    = threading.Lock()
check_existing_running = False

# ── MONITOR FILTERS (persistent across cycles) ────────────────────────────────────
# /filter  : only alert for products that have AT LEAST ONE of these sizes in stock
# /filterout: never alert for products that have ANY of these sizes
monitor_filter_sizes     = set()   # empty = no filter (show all)
monitor_filterout_sizes  = set()   # empty = no filterout
monitor_filters_lock     = threading.Lock()


# ── LISTING FETCH (PARALLEL) ──────────────────────────────────────────────────

def fetch_listing_page(page: int):
    params = {**LISTING_PARAMS, "currentPage": page}
    # IPv6 session — keeps listing traffic on a different IP than PDP
    resp = _ipv6_session.get(LISTING_URL, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return page, data.get("products", []), data.get("pagination", {}).get("totalPages", 1)


def fetch_all_listing_products(silent=False):
    try:
        t0 = time.time()
        _, first_products, total_pages = fetch_listing_page(0)
        results = [None] * total_pages
        results[0] = first_products

        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=total_pages - 1) as ex:
                futures = {ex.submit(fetch_listing_page, p): p for p in range(1, total_pages)}
                for future in as_completed(futures):
                    pg, prods, _ = future.result()
                    results[pg] = prods

        all_products = []
        for page_prods in results:
            if page_prods:
                all_products.extend(page_prods)

        if not silent:
            print(f"  📋 Listing: {len(all_products)} products across {total_pages} pages in {time.time()-t0:.1f}s")
        return all_products

    except Exception as e:
        print(f"[LISTING ERROR] {datetime.now():%H:%M:%S} — {e}")
        return None


# ── PDP FETCH ─────────────────────────────────────────────────────────────────

def fetch_pdp_stock(option_code: str) -> dict:
    """
    Returns {size: stock_level} for all sizes with stock > 0.
    Uses curl_cffi with Chrome TLS fingerprint + forced IPv4 to bypass Akamai 403.
    IPv4 is intentional — PDP endpoint blocks IPv6 more aggressively.
    """
    url = f"{PDP_BASE}/{option_code}"
    for attempt in range(3):
        try:
            session = get_cffi_session()
            if USE_CFFI:
                resp = pdp_get_ipv4(session, url, params=PDP_PARAMS, headers=HEADERS, timeout=15)
            else:
                resp = session.get(url, params=PDP_PARAMS, headers=HEADERS, timeout=15)

            if resp.status_code == 403:
                print(f"  [PDP 403] {option_code} — attempt {attempt+1}/3")
                time.sleep(attempt + 1)
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 3))
                time.sleep(retry_after)
                continue

            resp.raise_for_status()
            data = resp.json()

            size_stock = {}

            # variantOptions is primary, fall back to baseOptions[0].options
            variant_list = data.get("variantOptions", [])
            if not variant_list:
                base_options = data.get("baseOptions", [])
                if base_options:
                    variant_list = base_options[0].get("options", [])

            for variant in variant_list:
                sc_size = variant.get("scDisplaySize", "")
                if not sc_size:
                    for q in variant.get("variantOptionQualifiers", []):
                        val  = q.get("value", "")
                        name = q.get("name", "").lower()
                        if val and "color" not in name:
                            sc_size = val
                            break
                if not sc_size:
                    continue
                stock_info = variant.get("stock", {})
                status = stock_info.get("stockLevelStatus", "outOfStock")
                level  = int(stock_info.get("stockLevel", 0))
                if status in ("inStock", "lowStock") and level > 0:
                    size_stock[sc_size] = level

            return size_stock

        except Exception as e:
            print(f"  [PDP ERROR] {option_code} — {e}, attempt {attempt+1}/3")
            time.sleep(attempt + 1)

    print(f"  [PDP FAILED] {option_code} — gave up after 3 attempts")
    return {}


def fetch_pdp_stocks_parallel(option_codes: list) -> dict:
    """Fetch all PDP stocks simultaneously — curl_cffi handles TLS, no batching needed."""
    results = {}
    if not option_codes:
        return results
    with ThreadPoolExecutor(max_workers=min(len(option_codes), 100)) as ex:
        futures = {ex.submit(fetch_pdp_stock, code): code for code in option_codes}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def get_option_code(product: dict) -> str:
    return product.get("fnlColorVariantData", {}).get("colorGroup") or product["code"]


# ── TELEGRAM ─────────────────────────────────────────────────────────────────────

def send_telegram(message: str, chat_id: str = None):
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    chat_id or CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        # IPv6 session — Telegram traffic separate from PDP IPv4
        resp = _ipv6_session.post(url, json=payload, timeout=10)
        if not resp.ok:
            print(f"[TG ERROR] {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[TG ERROR] {e}")


def get_telegram_updates(offset: int) -> list:
    try:
        resp = _ipv6_session.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 1},
            timeout=5,
        )
        if resp.ok:
            return resp.json().get("result", [])
    except Exception:
        pass
    return []


# ── FORMATTERS ───────────────────────────────────────────────────────────────────

def size_icon(qty: int) -> str:
    if qty <= 3:  return "🔴"
    if qty <= 10: return "🟡"
    return "🟢"


def format_size_stock(size_stock: dict) -> list:
    if not size_stock:
        return ["  ⚫ No sizes in stock"]
    return [f"  {size_icon(qty)} {size}: {qty} Unit(s)" for size, qty in size_stock.items() if qty > 0]


def has_any_stock(size_stock: dict) -> bool:
    return any(qty > 0 for qty in size_stock.values())


def format_alert(product: dict, alert_type: str, size_stock: dict, change_lines: list = None) -> str:
    name        = product.get("name", "Unknown Product")
    code        = product.get("code", "N/A")
    product_url = f"{BASE_URL}{product.get('url', '')}"

    price_display = (
        product.get("price", {}).get("displayformattedValue")
        or product.get("price", {}).get("formattedValue", "N/A")
    )
    offer_data  = product.get("offerPrice", {})
    offer_price = offer_data.get("displayformattedValue") or offer_data.get("formattedValue", "")
    was_data    = product.get("wasPriceData", {})
    was_price   = was_data.get("displayformattedValue") or was_data.get("formattedValue", "")

    labels = [
        t.get("primary", {}).get("name")
        for t in product.get("tags", {}).get("categoryTags", [])
        if t.get("primary", {}).get("name")
    ]
    label_str  = " ".join(f"[{l}]" for l in labels) if labels else ""
    short_name = name if len(name) <= 60 else name[:57] + "..."

    headers = {
        "NEW":          f"🚨 <b>NEW PRODUCT</b> {label_str}",
        "RESTOCK":      f"🔄 <b>RESTOCKED</b> {label_str}",
        "STOCK_CHANGE": f"📈 <b>STOCK UPDATED</b> {label_str}",
        "EXISTING":     f"📦 <b>IN STOCK</b> {label_str}",
    }

    msg = [headers.get(alert_type, "🔔 <b>ALERT</b>"), ""]
    msg += [f"🏷️ {short_name}", f"🆔 Product ID: {code}", ""]

    msg.append(f"💰 Price: <b>{price_display}</b>")

    msg.append("")

    if alert_type == "STOCK_CHANGE":
        msg.append("📊 Stock Changes:")
        msg.extend(change_lines or [])
    else:
        msg.append("📊 Size Availability:")
        msg.extend(format_size_stock(size_stock))

    msg += ["", f"🔗 {product_url}", "", "@CoBra_SR"]
    return "\n".join(msg)


def stock_increased(old_snap: dict, new_snap: dict):
    changes = []
    for size, new_qty in new_snap.items():
        old_qty = old_snap.get(size, 0)
        if new_qty > old_qty:
            if old_qty == 0:
                changes.append(f"  {size_icon(new_qty)} {size}: back in stock ({new_qty} units)")
            else:
                changes.append(f"  {size_icon(new_qty)} {size}: {old_qty} → {new_qty} units")
    return len(changes) > 0, changes


# ── MONITOR FILTER HELPER ────────────────────────────────────────────────────────

def apply_monitor_filters(size_stock: dict):
    """
    Returns a (possibly trimmed) size_stock dict to display, or None if filtered out entirely.
    Logic:
      - If monitor_filterout_sizes set → skip product if it has ANY of those sizes
      - If monitor_filter_sizes set    → only show those sizes; skip product if none match
    """
    with monitor_filters_lock:
        fin  = set(monitor_filter_sizes)
        fout = set(monitor_filterout_sizes)

    # filterout: remove excluded sizes from display; suppress only if nothing remains
    if fout:
        size_stock = {s: q for s, q in size_stock.items() if s.upper() not in fout}
        if not size_stock:
            return None

    # filter: only show matching sizes, suppress if none match
    if fin:
        matched = {s: q for s, q in size_stock.items() if s.upper() in fin and q > 0}
        if not matched:
            return None
        return matched

    return size_stock


# ── SCAN HANDLER ─────────────────────────────────────────────────────────────────

def run_scan(chat_id: str, size_filter: list = None):
    global check_existing_running
    try:
        filter_label = " ".join(size_filter) if size_filter else "All"

        products = fetch_all_listing_products(silent=True)
        if not products:
            send_telegram("❌ Failed to fetch listing. Try again.", chat_id)
            return

        option_codes = [get_option_code(p) for p in products]
        prod_map     = {get_option_code(p): p for p in products}

        # Single merged message
        send_telegram(
            f"🔍 <b>Scanning all products...</b>\n"
            f"Size filter: <b>{filter_label}</b>\n\n"
            f"📋 Found <b>{len(products)}</b> products. Checking stock…",
            chat_id
        )

        t0          = time.time()
        pdp_results = fetch_pdp_stocks_parallel(option_codes)
        elapsed     = time.time() - t0

        in_stock_count = 0
        for code, size_stock in pdp_results.items():
            if not size_stock or not has_any_stock(size_stock):
                continue

            if size_filter:
                filter_upper = [s.upper() for s in size_filter]
                matched = {s: q for s, q in size_stock.items() if s.upper() in filter_upper and q > 0}
                if not matched:
                    continue
                display_stock = matched
            else:
                display_stock = size_stock

            product = prod_map.get(code)
            if product:
                send_telegram(format_alert(product, "EXISTING", display_stock), chat_id)
                in_stock_count += 1
                time.sleep(0.3)

        send_telegram(
            f"✅ <b>Scan complete</b> in {elapsed:.1f}s\n"
            f"📦 {in_stock_count} / {len(products)} products matched.\n"
            f"@CoBra_SR",
            chat_id
        )
        print(f"[{datetime.now():%H:%M:%S}] /scan done: {in_stock_count}/{len(products)} (filter={size_filter})")

    finally:
        check_existing_running = False


def handle_check_existing(chat_id: str, size_filter: list = None):
    global check_existing_running
    with check_existing_lock:
        if check_existing_running:
            send_telegram("⚠️ A scan is already running. Please wait.", chat_id)
            return
        check_existing_running = True
    threading.Thread(target=run_scan, args=(chat_id, size_filter), daemon=True).start()


# ── TELEGRAM LISTENER ─────────────────────────────────────────────────────────────

def telegram_listener(seen_snapshots: dict, listing_map: dict):
    global check_existing_running
    offset = 0
    print(f"[{datetime.now():%H:%M:%S}] 🤖 Telegram command listener started.")

    while True:
        updates = get_telegram_updates(offset)
        for update in updates:
            offset  = update["update_id"] + 1
            message = update.get("message", {})
            text    = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))

            if not text or not chat_id:
                continue

            # ── /check_existing → scan ALL, no filter ────────────────────────────
            if text == "/check_existing" or text == "/chex":
                print(f"[{datetime.now():%H:%M:%S}] 📥 /check_existing from {chat_id}")
                handle_check_existing(chat_id, size_filter=None)

            # ── /chex <sizes> → scan with size filter ────────────────────────────
            elif text.startswith("/chex "):
                parts = text[len("/chex"):].strip()
                sizes = [s.strip().upper() for s in parts.replace(",", " ").split() if s.strip()]
                if not sizes:
                    send_telegram(
                        "⚠️ Please include sizes after /chex\n"
                        "Example: <code>/chex M L XL</code> or <code>/chex 30 32 38</code>",
                        chat_id
                    )
                else:
                    print(f"[{datetime.now():%H:%M:%S}] 📐 /chex {sizes} from {chat_id}")
                    handle_check_existing(chat_id, size_filter=sizes)

            # ── /filterout <sizes> — MUST come before /filter check ───────────────
            elif text.startswith("/filterout"):
                parts = text[len("/filterout"):].strip()
                sizes = [s.strip().upper() for s in parts.replace(",", " ").split() if s.strip()]
                if not sizes:
                    send_telegram(
                        "⚠️ Please include sizes after /filterout\n"
                        "Example: <code>/filterout 38 40</code>",
                        chat_id
                    )
                else:
                    with monitor_filters_lock:
                        monitor_filterout_sizes.update(sizes)
                    current = sorted(monitor_filterout_sizes)
                    send_telegram(
                        f"🚫 <b>Filter-out set</b>\n"
                        f"❌ Hiding sizes from alerts: <b>{', '.join(current)}</b>\n"
                        f"Products with ONLY these sizes will be suppressed.\n"
                        f"Products with other sizes will still alert (filtered sizes hidden).\n\n"
                        f"Use /rfilterout to remove.\n"
                        f"@CoBra_SR",
                        chat_id
                    )
                    print(f"[{datetime.now():%H:%M:%S}] 🚫 /filterout set: {current}")

            # ── /filter <sizes> → only alert when product has these sizes ─────────
            elif text.startswith("/filter"):
                parts = text[len("/filter"):].strip()
                sizes = [s.strip().upper() for s in parts.replace(",", " ").split() if s.strip()]
                if not sizes:
                    send_telegram(
                        "⚠️ Please include sizes after /filter\n"
                        "Example: <code>/filter M L XL</code> or <code>/filter 38 40</code>",
                        chat_id
                    )
                else:
                    with monitor_filters_lock:
                        monitor_filter_sizes.update(sizes)
                    current = sorted(monitor_filter_sizes)
                    send_telegram(
                        f"✅ <b>Monitor filter set</b>\n"
                        f"🔍 Only alerting for sizes: <b>{', '.join(current)}</b>\n\n"
                        f"Use /rfilter to remove these filters.\n"
                        f"@CoBra_SR",
                        chat_id
                    )
                    print(f"[{datetime.now():%H:%M:%S}] 🔍 /filter set: {current}")

            # ── /rfilterout [sizes] → remove specific or all filterout sizes ──────
            elif text.startswith("/rfilterout"):
                parts = text[len("/rfilterout"):].strip()
                sizes = [s.strip().upper() for s in parts.replace(",", " ").split() if s.strip()]
                with monitor_filters_lock:
                    if sizes:
                        removed = [s for s in sizes if s in monitor_filterout_sizes]
                        monitor_filterout_sizes.difference_update(sizes)
                        remaining = sorted(monitor_filterout_sizes)
                        rm_str = ", ".join(removed) if removed else "nothing matched"
                        rem_str = ", ".join(remaining) if remaining else "None"
                        send_telegram(
                            f"✅ <b>Filter-out updated</b>\n"
                            f"🗑️ Removed: <b>{rm_str}</b>\n"
                            f"📋 Still filtered-out: <b>{rem_str}</b>\n"
                            f"@CoBra_SR",
                            chat_id
                        )
                    else:
                        removed = sorted(monitor_filterout_sizes)
                        monitor_filterout_sizes.clear()
                        send_telegram(
                            f"✅ <b>All filter-outs cleared</b>\n"
                            f"🔓 Removed: <b>{', '.join(removed) if removed else 'none was set'}</b>\n"
                            f"Now showing all sizes in alerts.\n"
                            f"@CoBra_SR",
                            chat_id
                        )
                print(f"[{datetime.now():%H:%M:%S}] 🔓 /rfilterout sizes={sizes or 'ALL'}")

            # ── /rfilter [sizes] → remove specific or all filter sizes ───────────
            elif text.startswith("/rfilter"):
                parts = text[len("/rfilter"):].strip()
                sizes = [s.strip().upper() for s in parts.replace(",", " ").split() if s.strip()]
                with monitor_filters_lock:
                    if sizes:
                        removed = [s for s in sizes if s in monitor_filter_sizes]
                        monitor_filter_sizes.difference_update(sizes)
                        remaining = sorted(monitor_filter_sizes)
                        rm_str = ", ".join(removed) if removed else "nothing matched"
                        rem_str = ", ".join(remaining) if remaining else "None (alerting all sizes)"
                        send_telegram(
                            f"✅ <b>Filter updated</b>\n"
                            f"🗑️ Removed: <b>{rm_str}</b>\n"
                            f"📋 Still filtering for: <b>{rem_str}</b>\n"
                            f"@CoBra_SR",
                            chat_id
                        )
                    else:
                        removed = sorted(monitor_filter_sizes)
                        monitor_filter_sizes.clear()
                        send_telegram(
                            f"✅ <b>All filters cleared</b>\n"
                            f"🔓 Removed: <b>{', '.join(removed) if removed else 'none was set'}</b>\n"
                            f"Now alerting for all sizes.\n"
                            f"@CoBra_SR",
                            chat_id
                        )
                print(f"[{datetime.now():%H:%M:%S}] 🔓 /rfilter sizes={sizes or 'ALL'}")

            # ── /flstat → show current filter/filterout status ───────────────────
            elif text == "/flstat":
                with monitor_filters_lock:
                    fin  = sorted(monitor_filter_sizes)
                    fout = sorted(monitor_filterout_sizes)

                filter_str    = ", ".join(fin)  if fin  else "None (all sizes)"
                filterout_str = ", ".join(fout) if fout else "None"

                send_telegram(
                    f"🔧 <b>Filter Status</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Filter (show only):   <b>{filter_str}</b>\n"
                    f"🚫 Filter-out (hide):    <b>{filterout_str}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"/rfilter — remove all or specific filter\n"
                    f"/rfilterout — remove all or specific filterout\n"
                    f"@CoBra_SR",
                    chat_id
                )
                print(f"[{datetime.now():%H:%M:%S}] 🔧 /flstat from {chat_id}")

            # ── /chstat → live stock snapshot from seen_snapshots ────────────────
            elif text == "/chstat":
                total     = len(seen_snapshots)
                in_stock  = sum(1 for v in seen_snapshots.values() if v and has_any_stock(v))
                oos       = sum(1 for v in seen_snapshots.values() if v is not None and not has_any_stock(v))
                unknown   = sum(1 for v in seen_snapshots.values() if v is None)
                absent    = 0  # we don't have absent_codes reference here, that's fine
                scan_status = "🔄 Running" if check_existing_running else "✅ Idle"

                send_telegram(
                    f"📊 <b>Current Stock Status</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📦 Total tracked:    <b>{total}</b>\n"
                    f"🟢 In stock:         <b>{in_stock}</b>\n"
                    f"⚫ Out of stock:     <b>{oos}</b>\n"
                    f"❓ Unknown (PDP ❌): <b>{unknown}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🔎 Manual scan:      {scan_status}\n"
                    f"Use /chex or /check_existing to deep scan\n"
                    f"Use /flstat to see active filters\n"
                    f"@CoBra_SR",
                    chat_id
                )
                print(f"[{datetime.now():%H:%M:%S}] 📊 /chstat from {chat_id}")

            elif text in ("/start", "/help"):
                send_telegram(
                    "👋 <b>SHEIN Men's Monitor Bot</b>\n\n"
                    "🤖 Auto-alerts for:\n"
                    "  🚨 New products (in stock)\n"
                    "  🔄 Restocked products\n"
                    "  📈 Stock increases\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<b>Manual Scan:</b>\n\n"
                    "📦 /check_existing — scan all products\n"
                    "🔍 /chex M L 38 — scan by size\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<b>Monitor Filters:</b>\n\n"
                    "✅ /filter &lt;sizes&gt;\n"
                    "    Alert ONLY if product has these sizes\n"
                    "    <code>/filter M L</code>\n\n"
                    "🚫 /filterout &lt;sizes&gt;\n"
                    "    Hide these sizes from alerts\n"
                    "    (still alerts if other sizes exist)\n"
                    "    <code>/filterout 38 40</code>\n\n"
                    "❌ /rfilter — clear all filters\n"
                    "❌ /rfilter M — remove only M from filter\n\n"
                    "❌ /rfilterout — clear all filterouts\n"
                    "❌ /rfilterout 38 — remove only 38\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<b>Status:</b>\n\n"
                    "📊 /chstat — live stock count\n"
                    "🔧 /flstat — active filter status\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "Size icons:\n"
                    "  🔴 1–3 units  🟡 4–10  🟢 11+\n\n"
                    "@CoBra_SR",
                    chat_id
                )

        time.sleep(1)



# ── STATE PERSISTENCE ─────────────────────────────────────────────────────────────

def save_state(seen_snapshots: dict, absent_codes: set):
    """Save state to disk so restarts don't re-alert already-seen products."""
    try:
        state = {
            "seen_snapshots": {k: v for k, v in seen_snapshots.items()},
            "absent_codes":   list(absent_codes),
            "saved_at":       datetime.now().isoformat(),
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[STATE] Save failed: {e}")


def load_state() -> tuple:
    """Load previous state. Returns (seen_snapshots, absent_codes) or empty dicts."""
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        seen = state.get("seen_snapshots", {})
        absent = set(state.get("absent_codes", []))
        saved_at = state.get("saved_at", "unknown")
        print(f"[STATE] Loaded {len(seen)} products from previous run (saved: {saved_at})")
        return seen, absent
    except FileNotFoundError:
        print("[STATE] No previous state found — starting fresh.")
        return {}, set()
    except Exception as e:
        print(f"[STATE] Load failed ({e}) — starting fresh.")
        return {}, set()

# ── RAILWAY HEALTH CHECK SERVER ───────────────────────────────────────────────

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass  # suppress HTTP access logs

def start_health_server():
    """
    Starts a minimal HTTP server on $PORT (Railway injects this env var).
    Railway requires something to respond on that port or it marks the deploy failed.
    Runs in a daemon thread — won't block or interfere with the monitor.
    """
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"✅ Health check server listening on port {port}")


# ── MAIN ────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  SHEIN Men's Category Monitor  |  @CoBra_SR")
    print("=" * 60)

    # Start Railway health check server first (required or deploy fails)
    start_health_server()

    # Load previous state first
    seen_snapshots, absent_codes = load_state()
    listing_map = {}

    print("\n📡 Fetching current listing (parallel)…")
    initial = fetch_all_listing_products()
    if initial is None:
        print("❌ Initial fetch failed. Exiting.")
        sys.exit(1)

    # Find truly new codes not in saved state
    current_codes_init = {get_option_code(p) for p in initial}
    new_since_restart  = current_codes_init - set(seen_snapshots.keys())
    # Also find codes with None snap (PDP previously failed) — need retry
    retry_on_start     = {c for c in current_codes_init if seen_snapshots.get(c) is None}
    needs_pdp_init     = new_since_restart | retry_on_start

    for p in initial:
        listing_map[get_option_code(p)] = p

    if needs_pdp_init:
        print(f"  🔍 Fetching PDP for {len(needs_pdp_init)} new/unknown products…")
        t0 = time.time()
        initial_pdp = fetch_pdp_stocks_parallel(list(needs_pdp_init))
        print(f"  ✅ PDP done in {time.time()-t0:.1f}s")
        for code, snap in initial_pdp.items():
            seen_snapshots[code] = snap if snap else None
    else:
        print(f"  ✅ All {len(current_codes_init)} products already known from previous run.")

    pdp_ok   = sum(1 for v in seen_snapshots.values() if v)
    pdp_none = sum(1 for v in seen_snapshots.values() if v is None)
    print(f"✅ Baseline: {len(seen_snapshots)} products (with stock: {pdp_ok} | unknown: {pdp_none}).")
    print(f"🔄 Monitoring every {CHECK_INTERVAL}s. Press Ctrl+C to stop.\n")

    threading.Thread(
        target=telegram_listener,
        args=(seen_snapshots, listing_map),
        daemon=True
    ).start()

    send_telegram(
        f"✅ <b>SHEIN Monitor Started</b>\n"
        f"👕 Category: Men's SHEINVERSE\n"
        f"📦 Baseline: {len(seen_snapshots)} products\n"
        f"⏱️ Interval: {CHECK_INTERVAL}s\n"
        f"💬 /check_existing or /chex — scan all stock\n"
        f"💬 /chex M L 38 — scan by size\n"
        f"💬 /filter M L — only alert these sizes\n"
        f"💬 /filterout 38 — suppress these sizes\n"
        f"💬 /chstat — check filter status\n"
        f"💬 /help — full command guide\n"
        f"@CoBra_SR"
    )

    while True:
        loop_start = time.time()

        products = fetch_all_listing_products(silent=True)
        if products is None:
            elapsed = time.time() - loop_start
            time.sleep(max(0, CHECK_INTERVAL - elapsed))
            continue

        current_map   = {get_option_code(p): p for p in products}
        current_codes = set(current_map.keys())

        # Codes never seen before
        brand_new = current_codes - set(seen_snapshots.keys())
        # Codes where baseline PDP failed (stored as None) — retry PDP this cycle
        pdp_retry = {c for c in current_codes if seen_snapshots.get(c) is None}
        # Codes that were absent and came back
        returned  = current_codes & absent_codes
        new_codes = brand_new  # only truly new codes get NEW alert
        needs_pdp = brand_new | pdp_retry | returned

        pdp_results = {}
        if needs_pdp:
            t0 = time.time()
            pdp_results = fetch_pdp_stocks_parallel(list(needs_pdp))
            elapsed = time.time()-t0
            for c in needs_pdp:
                cat = "brand_new" if c in brand_new else ("pdp_retry" if c in pdp_retry else "returned")
                snap = pdp_results.get(c)
                print(f"\n  🔍 [{cat}] {c} → snap={snap}")
            print(f"\n  🔍 PDP checked {len(needs_pdp)} in {elapsed:.1f}s")

        alerts_sent = 0

        for code, product in current_map.items():
            if code in brand_new:
                # Truly new product — never seen before
                new_snap = pdp_results.get(code, {})
                if new_snap and has_any_stock(new_snap):
                    display_snap = apply_monitor_filters(new_snap)
                    if display_snap is not None:
                        send_telegram(format_alert(product, "NEW", display_snap))
                        print(f"\n[{datetime.now():%H:%M:%S}] 🆕 NEW: {product.get('name', code)}")
                        alerts_sent += 1
                    else:
                        print(f"\n[{datetime.now():%H:%M:%S}] 🆕 NEW (filtered out): {product.get('name', code)}")
                else:
                    reason = "all sizes OOS" if new_snap == {} else "PDP failed — will retry"
                    print(f"\n[{datetime.now():%H:%M:%S}] 🆕 NEW (skipped — {reason}): {product.get('name', code)}")
                # Store None if PDP failed so we retry next cycle
                seen_snapshots[code] = new_snap if new_snap else None

            elif code in pdp_retry:
                # Previously failed PDP — try again now
                new_snap = pdp_results.get(code)
                if new_snap is not None:
                    if has_any_stock(new_snap):
                        display_snap = apply_monitor_filters(new_snap)
                        if display_snap is not None:
                            # Stock found — alert as NEW since we never had valid data
                            send_telegram(format_alert(product, "NEW", display_snap))
                            print(f"\n[{datetime.now():%H:%M:%S}] 🆕 NEW (PDP recovered): {product.get('name', code)}")
                            alerts_sent += 1
                    seen_snapshots[code] = new_snap  # update with real data (even if empty)
                # else PDP failed again — leave as None, will retry next cycle

            elif code in returned:
                new_snap = pdp_results.get(code, {})
                old_snap = seen_snapshots.get(code) or {}
                changed, change_lines = stock_increased(old_snap, new_snap)
                if changed and has_any_stock(new_snap):
                    display_snap = apply_monitor_filters(new_snap)
                    if display_snap is not None:
                        send_telegram(format_alert(product, "RESTOCK", display_snap, change_lines))
                        print(f"\n[{datetime.now():%H:%M:%S}] 🔄 RESTOCK: {product.get('name', code)}")
                        alerts_sent += 1
                    else:
                        print(f"\n[{datetime.now():%H:%M:%S}] 🔄 RESTOCK (filtered out): {product.get('name', code)}")
                absent_codes.discard(code)
                seen_snapshots[code] = new_snap

            listing_map[code] = product

        absent_codes.update(set(seen_snapshots.keys()) - current_codes)
        save_state(seen_snapshots, absent_codes)

        elapsed = time.time() - loop_start
        if alerts_sent == 0:
            msg = (
                f"[{datetime.now():%H:%M:%S}] No changes. "
                f"listing: {len(current_codes)} | absent: {len(absent_codes)} | "
                f"cycle: {elapsed:.2f}s"
            )
            print(f"\r{msg:<80}", end="", flush=True)

        # Sleep only the remaining time to maintain CHECK_INTERVAL cadence
        sleep_remaining = CHECK_INTERVAL - elapsed
        if sleep_remaining > 0:
            time.sleep(sleep_remaining)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Monitor stopped.")
        send_telegram("🛑 SHEIN Monitor stopped.\n@CoBra_SR")
