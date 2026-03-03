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
import queue
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
    """
    Returns a requests.Session with an enlarged connection pool.
    Pool size = 20 so parallel listing page fetches reuse TCP connections (keep-alive).
    This avoids a new TLS handshake on every cycle → shaves ~100-300ms per poll.
    """
    s = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=10,
        pool_maxsize=20,
        max_retries=0,          # we handle retries ourselves
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

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
CHECK_INTERVAL = 1
BASE_URL       = "https://sheinindia.in"
STATE_FILE     = "shein_state.json"  # saves seen_snapshots + absent_codes between restarts

# ── DEBUG MODE (set to True to see listing API structure) ─────────────────────────
DEBUG_LOG_LISTING = False  # Set to True on first run to see what data is available

# ── LISTING API ─────────────────────────────────────────────────────────────────
LISTING_URL = (
    "https://search-edge.services.sheinindia.in/rilfnlwebservices/v4/rilfnl/products/"
    "category/sverse-5939-37961"
)
LISTING_PARAMS = {
    "advfilter": "true", "urgencyDriverEnabled": "true",
    "query": ":newArrivals:genderfilter:Men", "pageSize": 60,
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
    "isNewUser": "true", "pincode": "743395",   # real pincode from app traffic
    "tagVersionTwo": "false", "applyExperiment": "true",  # fixed param name + true
    "fields": "FULL",
}
PDP_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "shein/6.9.0",
    "Client_type": "Android/32",
    "Client_version": "1.0.14",
    "X-Tenant-Id": "SHEIN",
    "Ad_id": "342f47d0-910f-4a29-9bd7-cadb98a2eca9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",   # from curl — reuse TCP connection
}

# ── CART API (add-to-cart stock check) ───────────────────────────────────────────
# Faster stock check — POST to cart. Success = in stock, error = OOS.
# Bearer token expires 2026-03-24. Cart ID is anonymous session, stable.
CART_BASE    = "https://api.services.sheinindia.in/rilfnlwebservices/v2/rilfnl/users/anonymous/carts"
CART_ID      = "96331ee6-ecc4-43f8-92fc-dbacf63d241b"
CART_TOKEN   = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJjbGllbnQiLCJjbGllbnROYW1lIjoidHJ1c3RlZF9jbGllbnQiLCJyb2xlcyI6W3sibmFtZSI6IlJPTEVfVFJVU1RFRF9DTElFTlQifV0sInRlbmFudElkIjoiU0hFSU4iLCJleHAiOjE3NzQzNzM4MDMsImlhdCI6MTc3MTc4MTgwM30.Dj1UvifYKjaFLh5b6YcWJ3Ad7mP6ooTZOVVaCkw3dc6BbnoyDlB6sjRWKF1_USwhH5wr5QJpDO2PM8j_OpDqzGR01LbRseL9wzHENkvnQXhlNfnjiuQIRUxwZ4QGNSfsD6wGfcek-LuYeTSVDO4mFbSzsr6KLV-e4PzEnXALa2EczZVc5CZvNVzgxveIeUZDgT-bf5ifuN3oMGO7PrzoVRb_7AcvwNcKL-0mArKidkEUimipN_Ypkd472NayOwY8M3Z7yneetdUKhGM9-sXlxtbLAKDt8dzPvz1btFypScEuRmsoK2epdFSbKShnljgdwkKBhIQiNuQFkP8pSBqMDQ"
CART_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "shein/6.9.0",
    "Client_type": "Android/32",
    "Client_version": "1.0.14",
    "X-Tenant-Id": "SHEIN",
    "Ad_id": "342f47d0-910f-4a29-9bd7-cadb98a2eca9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Content-Type": "application/x-www-form-urlencoded",
    "Authorization": f"Bearer {CART_TOKEN}",
}
CART_PARAMS  = {
    "client_type": "Android/32",
    "client_version": "1.0.14",
}

# ── HEADERS ─────────────────────────────────────────────────────────────────────
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "shein/6.9.0",
    "Client_type": "Android/32",
    "Client_version": "1.0.14",
    "X-Tenant-Id": "SHEIN",
    "Ad_id": "342f47d0-910f-4a29-9bd7-cadb98a2eca9",
    "Accept-Encoding": "gzip, deflate, br",
    # Extra headers from app traffic — server may use these for routing/caching
    "Usercohortvalues": "",
    "Os": "",
    "Ai": "",
    "Ua": "",
    "Vr": "",
}

# ── PER-PAGE LAST-MODIFIED CACHE ─────────────────────────────────────────────────
# Stores the Last-Modified timestamp returned by the server for each listing page.
# Sent back as If-Modified-Since next request — server returns 304 instantly if unchanged.
_page_last_modified: dict[int, str] = {}
_page_last_modified_lock = threading.Lock()

# ── SHARED STATE ─────────────────────────────────────────────────────────────────
check_existing_lock    = threading.Lock()
check_existing_running = False

# ── MONITOR FILTERS (persistent across cycles) ────────────────────────────────────
# /filter      : only alert for products that have AT LEAST ONE of these sizes in stock
# /filterout   : hide these sizes from alerts (suppress if no other sizes remain)
# /filterprice : suppress products whose price exceeds this value
# /flrange     : only alert for products whose price is within [min, max]
monitor_filter_sizes     = set()    # empty = no filter (show all)
monitor_filterout_sizes  = set()    # empty = no filterout
monitor_price_max        = None     # None = no upper price cap
monitor_price_range      = None     # None = no range; tuple (min, max) when set
monitor_filters_lock     = threading.Lock()

# ── PDP BACKGROUND WORKER QUEUE ───────────────────────────────────────────────────
# Listing loop enqueues (code, product, reason) tuples.
# A dedicated worker thread drains the queue, fetches PDP, updates seen_snapshots,
# and sends follow-up stock alerts — completely off the hot listing loop path.
pdp_queue            = queue.Queue()          # unbounded — worker drains as fast as it can
pdp_cache_lock       = threading.Lock()       # guards seen_snapshots + variant_cache writes

# ── VARIANT CODE CACHE ────────────────────────────────────────────────────────────
# {option_code: {size: variant_code}}  e.g. {"443392005_iceblue": {"M": "443392005012"}}
# Populated once from PDP. After that, restock scans use cart directly — no PDP needed.
variant_cache        = {}
VARIANT_CACHE_FILE   = "shein_variants.json"


# ── LISTING FETCH (PARALLEL) ──────────────────────────────────────────────────

def fetch_listing_page(page: int):
    params = {**LISTING_PARAMS, "currentPage": page}

    # Add If-Modified-Since if we have a cached timestamp for this page.
    # Server returns 304 Not Modified instantly (no body) if nothing changed — very fast.
    extra_headers = {}
    with _page_last_modified_lock:
        if page in _page_last_modified:
            extra_headers["If-Modified-Since"] = _page_last_modified[page]

    resp = _ipv6_session.get(
        LISTING_URL, params=params,
        headers={**HEADERS, **extra_headers},
        timeout=15
    )

    # 304 = nothing changed on this page since last fetch — return sentinel
    if resp.status_code == 304:
        return page, None, None   # None signals "unchanged" to caller

    resp.raise_for_status()

    # Cache the Last-Modified timestamp for next request
    last_mod = resp.headers.get("Last-Modified") or resp.headers.get("Date")
    if last_mod:
        with _page_last_modified_lock:
            _page_last_modified[page] = last_mod

    data = resp.json()
    return page, data.get("products", []), data.get("pagination", {}).get("totalPages", 1)


# Stores the last known full product list per page — used to serve 304 unchanged pages
_cached_page_products: dict[int, list] = {}
_cached_page_products_lock = threading.Lock()


def fetch_all_listing_products(silent=False):
    """
    Speed-optimised listing fetch with If-Modified-Since support:
    - Page 0 fetched first to get total_pages
    - All other pages fetched in parallel
    - 304 Not Modified → reuse last known products for that page (instant)
    - Last-Modified header cached per page for next request
    """
    try:
        t0 = time.time()
        _, first_products, total_pages = fetch_listing_page(0)

        # Handle page 0 result
        if first_products is None:
            # 304 — reuse cached page 0 products
            with _cached_page_products_lock:
                first_products = _cached_page_products.get(0, [])
        else:
            with _cached_page_products_lock:
                _cached_page_products[0] = first_products

        if total_pages is None:
            # total_pages unknown from 304 — use cached page count
            with _cached_page_products_lock:
                total_pages = max(_cached_page_products.keys(), default=0) + 1

        results = {0: first_products}

        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=total_pages - 1) as ex:
                futures = {ex.submit(fetch_listing_page, p): p for p in range(1, total_pages)}
                for future in as_completed(futures):
                    pg, prods, _ = future.result()
                    if prods is None:
                        # 304 — reuse cached
                        with _cached_page_products_lock:
                            prods = _cached_page_products.get(pg, [])
                    else:
                        with _cached_page_products_lock:
                            _cached_page_products[pg] = prods
                    results[pg] = prods

        all_products = [p for pg in sorted(results) for p in (results[pg] or [])]

        if not silent:
            print(f"  📋 Listing: {len(all_products)} products across {total_pages} pages in {time.time()-t0:.1f}s")
        return all_products

    except Exception as e:
        print(f"[LISTING ERROR] {datetime.now():%H:%M:%S} — {e}")
        return None


# ── PDP FETCH ─────────────────────────────────────────────────────────────────

def _parse_pdp_response(data: dict, return_variant_codes: bool = False):
    """
    Parse raw PDP JSON.
    return_variant_codes=False → {size: stock_level}  (normal use)
    return_variant_codes=True  → {size: (stock_level, variant_code)}  (for cart verify)
    """
    size_stock   = {}
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
        stock_info   = variant.get("stock", {})
        status       = stock_info.get("stockLevelStatus", "outOfStock")
        level        = int(stock_info.get("stockLevel", 0))
        variant_code = variant.get("code", "")   # e.g. "443392005015"
        if status in ("inStock", "lowStock") and level > 0:
            if return_variant_codes:
                size_stock[sc_size] = (level, variant_code)
            else:
                size_stock[sc_size] = level
    return size_stock


def fetch_pdp_stock(option_code: str, retries: int = 3) -> dict:
    """
    Primary entry point for stock checking.
    Delegates to fetch_pdp_stock_with_cart_verify:
      - PDP gives sizes + variant codes
      - Cart verifies each in-stock size in real-time (parallel)
      - Returns only confirmed in-stock sizes
    retries=3 for new products, retries=1 for background restock scan.
    """
    return fetch_pdp_stock_with_cart_verify(option_code, retries=retries)


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


# ── SIZE EXTRACTION FROM LISTING API ────────────────────────────────────────────
def extract_sizes_from_listing(product: dict) -> dict:
    """
    Extract available sizes directly from listing API response without PDP.
    Returns {size: stock_level} if available in listing data.
    
    Checks multiple possible fields where sizes might be stored in the mobile API response.
    """
    size_stock = {}
    
    # Try different possible fields where sizes might be stored in listing API
    
    # Field 1: Sale attributes (common in listing)
    sale_attrs = product.get("saleAttributes", [])
    if sale_attrs:
        for attr in sale_attrs:
            if isinstance(attr, dict):
                attr_name = attr.get("attributeName", "").lower()
                attr_value = attr.get("attributeValue", "")
                # Look for size-related attributes
                if any(x in attr_name for x in ["size", "length", "fit", "bust", "waist"]):
                    if attr_value:
                        size_stock[attr_value] = 1
    
    # Field 2: Variants (product variants with stock)
    variants = product.get("variants", [])
    if variants:
        for variant in variants:
            size_val = variant.get("size") or variant.get("sizeId") or variant.get("displaySize")
            stock = variant.get("stock", {})
            stock_level = stock.get("stockLevel", 0) if isinstance(stock, dict) else 1
            if size_val and stock_level > 0:
                size_stock[str(size_val)] = stock_level
    
    # Field 3: Attribute values (common structure)
    attr_vals = product.get("attributeValues", {})
    if isinstance(attr_vals, dict):
        for key, value in attr_vals.items():
            if any(x in key.lower() for x in ["size", "length", "fit", "bust", "waist"]):
                if isinstance(value, dict):
                    size_val = value.get("value")
                    if size_val:
                        size_stock[str(size_val)] = 1
                elif value:
                    size_stock[str(value)] = 1
    
    # Field 4: Sale attribute list (variant options)
    sale_attr_list = product.get("saleAttributeList", [])
    if sale_attr_list and isinstance(sale_attr_list, list):
        for attr in sale_attr_list:
            if isinstance(attr, dict):
                attr_name = attr.get("saleName", "").lower()
                if any(x in attr_name for x in ["size", "length", "fit", "bust", "waist"]):
                    values = attr.get("saleAttributeValues", [])
                    if values and isinstance(values, list):
                        for v in values:
                            if isinstance(v, dict):
                                size_val = v.get("name") or v.get("value")
                                if size_val:
                                    size_stock[str(size_val)] = 1
    
    # Field 5: Options (sometimes variants are under options)
    options = product.get("options", [])
    if options and isinstance(options, list):
        for opt in options:
            if isinstance(opt, dict):
                opt_name = opt.get("optionName", "").lower()
                if any(x in opt_name for x in ["size", "length", "fit", "bust", "waist"]):
                    opt_values = opt.get("optionValues", [])
                    if opt_values and isinstance(opt_values, list):
                        for ov in opt_values:
                            if isinstance(ov, dict):
                                size_val = ov.get("value") or ov.get("name")
                                if size_val:
                                    size_stock[str(size_val)] = 1
    
    return size_stock


def log_listing_sample(products: list, num_samples: int = 3):
    """
    Log detailed structure of first N products from listing to understand data format.
    Run this once to understand what fields are available.
    
    This helps debug and see all available data in listing API without needing PDP.
    """
    import json
    
    if not products or num_samples <= 0:
        return
    
    print("\n" + "="*80)
    print(f"📊 LISTING API SAMPLE STRUCTURE (first {num_samples} products)")
    print("="*80)
    
    for i, product in enumerate(products[:num_samples]):
        code = get_option_code(product)
        name = product.get("name", "Unknown")
        print(f"\n[Product {i+1}] Code: {code}")
        print(f"  Name: {name[:60]}")
        print(f"  All top-level keys: {list(product.keys())}")
        
        # Show size-related fields if present
        size_fields = ["variants", "saleAttributes", "attributeValues", 
                      "saleAttributeList", "options", "attributes"]
        found_any = False
        for key in size_fields:
            if key in product:
                val = product[key]
                found_any = True
                if isinstance(val, (list, dict)):
                    print(f"    ✓ {key}: {type(val).__name__} (length: {len(val)})")
                    if isinstance(val, list) and val:
                        # Show first item structure
                        first_item = val[0]
                        if isinstance(first_item, dict):
                            print(f"      First item keys: {list(first_item.keys())}")
                            print(f"      Sample: {json.dumps(first_item, indent=8, default=str)[:300]}...")
                    elif isinstance(val, dict) and val:
                        first_key = list(val.keys())[0]
                        print(f"      Sample: {first_key} = {str(val[first_key])[:80]}...")
                else:
                    print(f"    ✓ {key}: {str(val)[:80]}...")
        
        if not found_any:
            print(f"    ❌ No size-related fields found in this product")
        
        # Try to extract sizes and show result
        extracted = extract_sizes_from_listing(product)
        if extracted:
            print(f"    🎯 EXTRACTED SIZES: {extracted}")
        else:
            print(f"    ❌ No sizes extracted from available fields")
    
    print("\n" + "="*80 + "\n")


# ── CART-BASED STOCK VERIFICATION ────────────────────────────────────────────────

def _cart_verify_variant(variant_code: str) -> bool:
    """
    POST one variant to cart. Returns True if server accepts it (in stock).
    200/201 = in stock. Anything else = OOS or error.
    """
    url  = f"{CART_BASE}/{CART_ID}/entries"
    data = f"code={variant_code}&qty=1&sourceStoreId=shein&fields=FULL"
    try:
        resp = _ipv6_session.post(
            url, params=CART_PARAMS, headers=CART_HEADERS,
            data=data, timeout=8,
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"  [CART ERROR] {variant_code}: {e}")
        return False


def _cart_verify_all(size_vcodes: dict) -> dict:
    """
    Given {size: (stock_level, variant_code)}, cart-verify all in parallel.
    Returns {size: stock_level} for cart-confirmed in-stock sizes only.
    Falls back to PDP result if cart returns nothing (token expired/all failed).
    """
    confirmed = {}
    with ThreadPoolExecutor(max_workers=max(len(size_vcodes), 1)) as ex:
        futures = {
            ex.submit(_cart_verify_variant, vcode): (size, level)
            for size, (level, vcode) in size_vcodes.items()
            if vcode
        }
        for future in as_completed(futures):
            size, level = futures[future]
            try:
                if future.result():
                    confirmed[size] = level
            except Exception:
                pass
    # If nothing confirmed (token expired / all 4xx), fall back to PDP result
    if not confirmed:
        confirmed = {s: lv for s, (lv, _) in size_vcodes.items()}
    return confirmed


def fetch_pdp_stock_with_cart_verify(option_code: str, retries: int = 3) -> dict:
    """
    Smart two-phase stock check:

    PHASE A — variant codes already cached (fast path):
      Skip PDP entirely. Cart-verify all known variants directly.
      → Only cart calls, no PDP round trip.

    PHASE B — variant codes not cached yet (first time):
      1. PDP fetch → parse ALL variants (not just in-stock) → cache ALL variant codes
      2. Cart-verify the PDP-in-stock ones in parallel → confirmed sizes
      → After this, future checks use Phase A forever.

    Falls back to PDP-only if cart token expired or all cart calls fail.
    """
    with pdp_cache_lock:
        cached_variants = variant_cache.get(option_code)   # {size: variant_code} or None

    # ── PHASE A: cart-only (no PDP needed) ───────────────────────────────────────
    if cached_variants:
        # Build size_vcodes with dummy stock level — cart result is the truth
        # We don't know current stock level without PDP, so use 1 as placeholder
        # Real level comes from PDP on first fetch and is stored in seen_snapshots
        size_vcodes = {size: (1, vcode) for size, vcode in cached_variants.items()}
        confirmed   = {}
        with ThreadPoolExecutor(max_workers=max(len(size_vcodes), 1)) as ex:
            futures = {
                ex.submit(_cart_verify_variant, vcode): size
                for size, vcode in cached_variants.items()
                if vcode
            }
            for future in as_completed(futures):
                size = futures[future]
                try:
                    if future.result():
                        confirmed[size] = 1   # in stock — level unknown without PDP
                except Exception:
                    pass
        # If cart returned nothing fall back to PDP below
        if confirmed:
            return confirmed
        # else fall through to Phase B (PDP) — cart may have failed

    # ── PHASE B: PDP first, cache variants, then cart verify ─────────────────────
    url = f"{PDP_BASE}/{option_code}"
    for attempt in range(retries):
        try:
            session = get_cffi_session()
            if USE_CFFI:
                resp = pdp_get_ipv4(session, url, params=PDP_PARAMS, headers=PDP_HEADERS, timeout=15)
            else:
                resp = session.get(url, params=PDP_PARAMS, headers=PDP_HEADERS, timeout=15)

            if resp.status_code == 403:
                if retries > 1:
                    print(f"  [PDP 403] {option_code} — attempt {attempt+1}/{retries}")
                    time.sleep(attempt + 1)
                    continue
                return None

            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("Retry-After", 3)))
                continue

            resp.raise_for_status()
            data = resp.json()

            # Parse ALL variants (in-stock and OOS) to cache their codes
            all_variants = {}
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
                vcode = variant.get("code", "")
                if sc_size and vcode:
                    all_variants[sc_size] = vcode   # cache ALL sizes, even OOS ones

            # Cache variant codes for this product
            if all_variants:
                with pdp_cache_lock:
                    variant_cache[option_code] = all_variants
                print(f"  [VARIANT] Cached {len(all_variants)} variant codes for {option_code}")

            # Get PDP in-stock sizes for cart verification
            pdp_in_stock = _parse_pdp_response(data, return_variant_codes=True)
            # {size: (stock_level, variant_code)}

            if not pdp_in_stock:
                return {}   # PDP says all OOS

            if CART_TOKEN:
                return _cart_verify_all(pdp_in_stock)
            else:
                return {s: lv for s, (lv, _) in pdp_in_stock.items()}

        except Exception as e:
            if retries > 1:
                print(f"  [PDP ERROR] {option_code} — {e}, attempt {attempt+1}/{retries}")
            time.sleep(attempt + 1)

    if retries > 1:
        print(f"  [PDP FAILED] {option_code} — gave up after {retries} attempts")
    return {}


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


# ── PRICE EXTRACTION HELPER ──────────────────────────────────────────────────────

def get_product_price(product: dict):
    """
    Extract numeric price from a product dict.
    Tries offerPrice first (discounted), then regular price.
    Returns float or None if unparseable.
    """
    import re
    for key in ("offerPrice", "price"):
        data = product.get(key, {})
        raw  = data.get("value") or data.get("displayformattedValue") or data.get("formattedValue", "")
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            cleaned = re.sub(r"[^\d.]", "", raw)
            if cleaned:
                try:
                    return float(cleaned)
                except ValueError:
                    pass
    return None


# ── MONITOR FILTER HELPER ────────────────────────────────────────────────────────

def apply_monitor_filters(size_stock: dict, product: dict = None):
    """
    Returns a (possibly trimmed) size_stock dict to display, or None if filtered out entirely.
    Logic:
      - filterprice  → suppress if product price > max
      - flrange      → suppress if product price outside [min, max]
      - filterout    → hide those sizes; suppress if nothing remains
      - filter       → only show matching sizes; suppress if none match
    """
    with monitor_filters_lock:
        fin        = set(monitor_filter_sizes)
        fout       = set(monitor_filterout_sizes)
        price_max  = monitor_price_max
        price_rng  = monitor_price_range

    # price filters
    if product is not None and (price_max is not None or price_rng is not None):
        price = get_product_price(product)
        if price is not None:
            if price_max is not None and price > price_max:
                return None
            if price_rng is not None:
                lo, hi = price_rng
                if not (lo <= price <= hi):
                    return None

    # filterout sizes
    if fout:
        size_stock = {s: q for s, q in size_stock.items() if s.upper() not in fout}
        if not size_stock:
            return None

    # filter sizes
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

            # ── /filterprice <amount> → suppress products above this price ─────
            elif text.startswith("/filterprice"):
                parts = text[len("/filterprice"):].strip()
                if not parts:
                    send_telegram(
                        "⚠️ Please include a price after /filterprice\n"
                        "Example: <code>/filterprice 600</code>",
                        chat_id
                    )
                else:
                    try:
                        max_price = float(parts.replace(",", "").strip())
                        with monitor_filters_lock:
                            monitor_price_max   = max_price
                            monitor_price_range = None   # clear range if set
                        send_telegram(
                            f"💰 <b>Price filter set</b>\n"
                            f"🔽 Only alerting for products <b>≤ ₹{max_price:,.0f}</b>\n"
                            f"Products above this price will be skipped.\n\n"
                            f"Use /rflprice to remove.\n"
                            f"@CoBra_SR",
                            chat_id
                        )
                        print(f"[{{datetime.now():%H:%M:%S}}] 💰 /filterprice set: ≤{max_price}")
                    except ValueError:
                        send_telegram("❌ Invalid price. Example: <code>/filterprice 600</code>", chat_id)

            # ── /flrange <min> <max> → only alert within price range ─────────────
            elif text.startswith("/flrange"):
                parts = text[len("/flrange"):].strip()
                nums  = [x.replace(",", "").strip() for x in parts.split()]
                if len(nums) != 2:
                    send_telegram(
                        "⚠️ Please include min and max prices after /flrange\n"
                        "Example: <code>/flrange 100 300</code>",
                        chat_id
                    )
                else:
                    try:
                        lo, hi = float(nums[0]), float(nums[1])
                        if lo > hi:
                            lo, hi = hi, lo
                        with monitor_filters_lock:
                            monitor_price_range = (lo, hi)
                            monitor_price_max   = None   # clear max if set
                        send_telegram(
                            f"💰 <b>Price range set</b>\n"
                            f"🔁 Only alerting for products between <b>₹{lo:,.0f} – ₹{hi:,.0f}</b>\n"
                            f"Products outside this range will be skipped.\n\n"
                            f"Use /rflprice to remove.\n"
                            f"@CoBra_SR",
                            chat_id
                        )
                        print(f"[{{datetime.now():%H:%M:%S}}] 💰 /flrange set: {lo}–{hi}")
                    except ValueError:
                        send_telegram("❌ Invalid prices. Example: <code>/flrange 100 300</code>", chat_id)

            # ── /rflprice → remove price filter (both filterprice and flrange) ───
            elif text == "/rflprice":
                with monitor_filters_lock:
                    had_max   = monitor_price_max
                    had_range = monitor_price_range
                    monitor_price_max   = None
                    monitor_price_range = None
                if had_max is not None:
                    removed_str = f"max price ≤ ₹{had_max:,.0f}"
                elif had_range is not None:
                    removed_str = f"range ₹{had_range[0]:,.0f} – ₹{had_range[1]:,.0f}"
                else:
                    removed_str = "none was set"
                send_telegram(
                    f"✅ <b>Price filter cleared</b>\n"
                    f"🔓 Removed: <b>{removed_str}</b>\n"
                    f"Now alerting for all price ranges.\n"
                    f"@CoBra_SR",
                    chat_id
                )
                print(f"[{{datetime.now():%H:%M:%S}}] 🔓 /rflprice cleared")

            # ── /flstat → show all active filters ────────────────────────────────
            elif text == "/flstat":
                with monitor_filters_lock:
                    fin        = sorted(monitor_filter_sizes)
                    fout       = sorted(monitor_filterout_sizes)
                    price_max  = monitor_price_max
                    price_rng  = monitor_price_range

                filter_str    = ", ".join(fin)  if fin  else "None (all sizes)"
                filterout_str = ", ".join(fout) if fout else "None"
                if price_max is not None:
                    price_str = f"≤ ₹{price_max:,.0f}  (/filterprice)"
                elif price_rng is not None:
                    price_str = f"₹{price_rng[0]:,.0f} – ₹{price_rng[1]:,.0f}  (/flrange)"
                else:
                    price_str = "None (all prices)"

                send_telegram(
                    f"🔧 <b>Active Filter Status</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Size filter:       <b>{filter_str}</b>\n"
                    f"🚫 Size filter-out:  <b>{filterout_str}</b>\n"
                    f"💰 Price filter:     <b>{price_str}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"/rfilter [size] — remove size filter\n"
                    f"/rfilterout [size] — remove filterout\n"
                    f"/rflprice — remove price filter\n"
                    f"@CoBra_SR",
                    chat_id
                )
                print(f"[{{datetime.now():%H:%M:%S}}] 🔧 /flstat from {chat_id}")

            # ── /chstat → live stock snapshot from seen_snapshots ────────────────
            elif text == "/chstat":
                total     = len(seen_snapshots)
                in_stock  = sum(1 for v in seen_snapshots.values() if v and has_any_stock(v))
                oos       = sum(1 for v in seen_snapshots.values() if v is not None and not has_any_stock(v))
                unknown   = sum(1 for v in seen_snapshots.values() if v is None)
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
                print(f"[{{datetime.now():%H:%M:%S}}] 📊 /chstat from {chat_id}")

            elif text in ("/start", "/help"):
                send_telegram(
                    "👋 <b>SHEIN Men's Monitor Bot</b>\n\n"
                    "🤖 Auto-alerts for:\n"
                    "  🚨 New products (in stock)\n"
                    "  🔄 Restocked products\n"
                    "  📈 Stock increases\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<b>📦 Manual Scan:</b>\n\n"
                    "/check_existing — scan all products\n"
                    "/chex M L 38 — scan by size\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<b>📐 Size Filters:</b>\n\n"
                    "/filter &lt;sizes&gt;\n"
                    "  Alert ONLY if product has these sizes\n"
                    "  <code>/filter M L XL</code>\n\n"
                    "/filterout &lt;sizes&gt;\n"
                    "  Hide these sizes from alerts\n"
                    "  (still alerts if other sizes exist)\n"
                    "  <code>/filterout 38 40</code>\n\n"
                    "/rfilter — clear all size filters\n"
                    "/rfilter M — remove only M\n\n"
                    "/rfilterout — clear all filterouts\n"
                    "/rfilterout 38 — remove only 38\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<b>💰 Price Filters:</b>\n\n"
                    "/filterprice &lt;amount&gt;\n"
                    "  Only alert for products ≤ price\n"
                    "  <code>/filterprice 600</code>\n\n"
                    "/flrange &lt;min&gt; &lt;max&gt;\n"
                    "  Only alert within price range\n"
                    "  <code>/flrange 100 300</code>\n\n"
                    "/rflprice — remove price filter\n\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<b>📊 Status Commands:</b>\n\n"
                    "/chstat — live stock count\n"
                    "/flstat — all active filters\n\n"
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

    # Save variant cache separately — grows over time, no need to rewrite every cycle
    try:
        with pdp_cache_lock:
            vc_snapshot = dict(variant_cache)
        with open(VARIANT_CACHE_FILE, "w") as f:
            json.dump(vc_snapshot, f)
    except Exception as e:
        print(f"[STATE] Variant cache save failed: {e}")


def load_state() -> tuple:
    """Load previous state. Returns (seen_snapshots, absent_codes) or empty dicts."""
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        seen = state.get("seen_snapshots", {})
        absent = set(state.get("absent_codes", []))
        saved_at = state.get("saved_at", "unknown")
        print(f"[STATE] Loaded {len(seen)} products from previous run (saved: {saved_at})")
    except FileNotFoundError:
        print("[STATE] No previous state found — starting fresh.")
        seen, absent = {}, set()
    except Exception as e:
        print(f"[STATE] Load failed ({e}) — starting fresh.")
        seen, absent = {}, set()

    # Load variant cache
    try:
        with open(VARIANT_CACHE_FILE, "r") as f:
            loaded_vc = json.load(f)
        with pdp_cache_lock:
            variant_cache.update(loaded_vc)
        print(f"[STATE] Loaded variant codes for {len(loaded_vc)} products")
    except FileNotFoundError:
        print("[STATE] No variant cache found — will build from PDP.")
    except Exception as e:
        print(f"[STATE] Variant cache load failed ({e})")

    return seen, absent

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


# ── PDP BACKGROUND WORKER ────────────────────────────────────────────────────────

def pdp_worker(seen_snapshots: dict, listing_map: dict):
    """
    Two jobs in one thread:

    JOB 1 — Process pdp_queue (priority):
      New/returned products detected by listing loop.
      reason="new"      → alert as NEW if in stock
      reason="returned" → alert as RESTOCK if sizes increased
      reason="retry"    → PDP previously failed, try again silently

    JOB 2 — Continuous restock scan of ALL cached products:
      After draining the queue, re-check every cached product's sizes.
      If any size has more stock than last snapshot → RESTOCK alert.
      This is how OOS→in-stock changes are caught for existing products.
    """
    print(f"[{datetime.now():%H:%M:%S}] 🔧 PDP background worker started.")

    while True:
        # ── JOB 1: drain queue first (new products take priority) ────────────────
        while not pdp_queue.empty():
            try:
                code, product, reason, old_snap, pdp_retries = pdp_queue.get(block=False)
            except queue.Empty:
                break

            try:
                snap = fetch_pdp_stock(code, retries=pdp_retries)
                name = product.get("name", code)

                with pdp_cache_lock:
                    seen_snapshots[code] = snap   # cache result (even {} = confirmed OOS)

                if snap and has_any_stock(snap):
                    display_snap = apply_monitor_filters(snap, product)
                    if display_snap is not None:
                        if reason in ("new", "retry"):
                            send_telegram(format_alert(product, "NEW", display_snap))
                            print(f"\n[PDP-WORKER] ✅ NEW in stock: {name}")
                        elif reason == "returned":
                            _, change_lines = stock_increased(old_snap or {}, snap)
                            send_telegram(format_alert(product, "RESTOCK", display_snap, change_lines))
                            print(f"\n[PDP-WORKER] 🔄 RESTOCK (returned): {name}")
                    else:
                        print(f"\n[PDP-WORKER] ⛔ Filtered: {name}")
                elif snap is not None:
                    if reason != "retry":
                        print(f"\n[PDP-WORKER] ⚫ OOS: {name}")
                else:
                    # PDP failed — keep as None so listing loop re-queues next cycle
                    with pdp_cache_lock:
                        seen_snapshots[code] = None
                    print(f"\n[PDP-WORKER] ⚠️ PDP failed, will retry: {code}")

            except Exception as e:
                print(f"\n[PDP-WORKER ERROR] new/returned {code}: {e}")
                with pdp_cache_lock:
                    seen_snapshots[code] = None
            finally:
                pdp_queue.task_done()

        # ── JOB 2: restock scan — re-check all cached products once per pass ─────
        # Take a snapshot of current codes so we don't hold the lock during PDP calls
        with pdp_cache_lock:
            restock_targets = [
                (code, snap)
                for code, snap in seen_snapshots.items()
                if snap is not None   # skip PDP-pending items
            ]

        for code, old_snap in restock_targets:
            # If a new item arrived in the queue, pause restock scan to handle it first
            if not pdp_queue.empty():
                break

            product = listing_map.get(code)
            if not product:
                continue

            try:
                # Single attempt only — no retries during restock scan.
                # If 403/fail, skip this product and revisit next pass.
                new_snap = fetch_pdp_stock(code, retries=1)

                # Only update cache on valid response — None means 403/fail, keep old value
                if new_snap is not None:
                    with pdp_cache_lock:
                        seen_snapshots[code] = new_snap

                name = product.get("name", code)

                if new_snap and has_any_stock(new_snap):
                    changed, change_lines = stock_increased(old_snap or {}, new_snap)
                    if changed:
                        display_snap = apply_monitor_filters(new_snap, product)
                        if display_snap is not None:
                            send_telegram(format_alert(product, "RESTOCK", display_snap, change_lines))
                            print(f"\n[PDP-WORKER] 🔄 RESTOCK detected: {name}")
                        else:
                            print(f"\n[PDP-WORKER] ⛔ Restock filtered: {name}")
                # None = 403/fail (cache unchanged), {} = confirmed OOS (cache updated above)

            except Exception as e:
                print(f"\n[PDP-WORKER ERROR] restock scan {code}: {e}")

            # Throttle between restock scan requests — avoids Akamai 403 rate limit
            # 0.5–1.0s gap means ~43 products scanned per 30–60s (plenty fast for restocks)
            time.sleep(random.uniform(0.5, 1.0))

            # Check queue again between each product
            if not pdp_queue.empty():
                break

        # Brief pause before starting next full pass
        time.sleep(0.5)


# ── MAIN ────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  SHEIN Men's Category Monitor  |  @CoBra_SR")
    print("=" * 60)

    # Check cart token expiry at startup
    try:
        import base64 as _b64, json as _j, datetime as _dt
        payload = CART_TOKEN.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        exp = _j.loads(_b64.b64decode(payload)).get("exp", 0)
        days_left = (exp - _dt.datetime.now().timestamp()) / 86400
        exp_date  = _dt.datetime.fromtimestamp(exp).strftime("%Y-%m-%d")
        if days_left <= 0:
            print(f"❌ CART TOKEN EXPIRED on {exp_date}! Cart stock checks disabled.")
            send_telegram(f"❌ <b>Cart token EXPIRED</b> on {exp_date}\nUpdate CART_TOKEN in script!\n@CoBra_SR")
        elif days_left < 7:
            print(f"⚠️  Cart token expires in {days_left:.0f} days ({exp_date})!")
            send_telegram(f"⚠️ <b>Cart token expiring soon</b> — {days_left:.0f} days left ({exp_date})\nUpdate CART_TOKEN!\n@CoBra_SR")
        else:
            print(f"✅ Cart token valid — expires {exp_date} ({days_left:.0f} days)")
    except Exception as e:
        print(f"⚠️  Could not check cart token expiry: {e}")

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

    # DEBUG: Log listing API structure if enabled
    if DEBUG_LOG_LISTING:
        print("\n🔍 DEBUG MODE: Analyzing listing API structure...")
        log_listing_sample(initial, num_samples=5)
        print("👆 Review the structure above to understand what size data is available.")
        print("   Once you see the pattern, update extract_sizes_from_listing() accordingly.")
        print("   Then set DEBUG_LOG_LISTING = False to disable this logging.\n")

    # Find truly new codes not in saved state
    current_codes_init = {get_option_code(p) for p in initial}
    new_since_restart  = current_codes_init - set(seen_snapshots.keys())
    # Also find codes with None snap (PDP previously failed) — need retry
    retry_on_start     = {c for c in current_codes_init if seen_snapshots.get(c) is None}
    needs_pdp_init     = new_since_restart | retry_on_start

    for p in initial:
        listing_map[get_option_code(p)] = p

    if needs_pdp_init:
        print(f"  🔍 {len(needs_pdp_init)} new products will be PDP-checked by background worker…")
        for code in needs_pdp_init:
            seen_snapshots[code] = None   # sentinel: PDP pending
    else:
        print(f"  ✅ All {len(current_codes_init)} products already known from previous run.")

    pdp_ok   = sum(1 for v in seen_snapshots.values() if v)
    pdp_none = sum(1 for v in seen_snapshots.values() if v is None)
    print(f"✅ Baseline: {len(seen_snapshots)} products (with stock: {pdp_ok} | unknown: {pdp_none}).")
    print(f"🔄 Monitoring every {CHECK_INTERVAL}s. Press Ctrl+C to stop.\n")

    # Start PDP background worker — handles all stock checks off the listing loop
    threading.Thread(
        target=pdp_worker,
        args=(seen_snapshots, listing_map),
        daemon=True
    ).start()

    threading.Thread(
        target=telegram_listener,
        args=(seen_snapshots, listing_map),
        daemon=True
    ).start()

    # Enqueue startup PDP checks for new/unknown products
    for code in needs_pdp_init:
        product = listing_map.get(code)
        if product:
            pdp_queue.put((code, product, "retry", None, 1))
    if needs_pdp_init:
        print(f"  📥 Queued {len(needs_pdp_init)} PDP checks for background worker.")

    send_telegram(
        f"✅ <b>SHEIN Monitor Started</b>\n"
        f"👕 Category: Men's SHEINVERSE\n"
        f"📦 Baseline: {len(seen_snapshots)} products\n"
        f"⏱️ Interval: {CHECK_INTERVAL}s\n"
        f"💬 /check_existing — scan all stock\n"
        f"💬 /chex M L 38 — scan by size\n"
        f"💬 /filter M — only alert this size\n"
        f"💬 /filterout 38 — hide this size\n"
        f"💬 /filterprice 600 — max price filter\n"
        f"💬 /flrange 100 300 — price range filter\n"
        f"💬 /chstat — stock status\n"
        f"💬 /flstat — active filters\n"
        f"💬 /help — full command guide\n"
        f"@CoBra_SR"
    )

    # Tracks codes currently sitting in the PDP queue — avoids double-queuing
    pdp_queued = set()

    while True:
        loop_start = time.time()

        products = fetch_all_listing_products(silent=True)
        if products is None:
            elapsed = time.time() - loop_start
            time.sleep(max(0, CHECK_INTERVAL - elapsed))
            continue

        current_map   = {get_option_code(p): p for p in products}
        current_codes = set(current_map.keys())

        # Update listing_map with latest product data
        listing_map.update(current_map)

        with pdp_cache_lock:
            cached_codes = set(seen_snapshots.keys())

        # ── Only care about codes not yet in cache ────────────────────────────────
        # brand_new  : never seen before
        # returned   : was absent (dropped off listing), now back
        # Both get queued for PDP — everything else already has a cached result
        brand_new = current_codes - cached_codes
        returned  = (current_codes & absent_codes) - pdp_queued

        new_queued = 0

        for code in brand_new:
            if code in pdp_queued:
                continue   # already in queue, worker hasn't processed it yet
            product = current_map[code]
            # Mark immediately so next cycle doesn't re-detect it
            with pdp_cache_lock:
                seen_snapshots[code] = None   # None = PDP in progress
            pdp_queued.add(code)
            pdp_queue.put((code, product, "new", None, 3))
            new_queued += 1
            print(f"\n[{datetime.now():%H:%M:%S}] 📥 New → PDP queued: {product.get('name', code)}")

        for code in returned:
            product  = current_map[code]
            old_snap = seen_snapshots.get(code) or {}
            absent_codes.discard(code)
            with pdp_cache_lock:
                seen_snapshots[code] = None
            pdp_queued.add(code)
            pdp_queue.put((code, product, "returned", old_snap, 3))
            new_queued += 1
            print(f"\n[{datetime.now():%H:%M:%S}] 🔄 Returned → PDP queued: {product.get('name', code)}")

        # Track products that dropped off listing → mark absent
        newly_absent = cached_codes - current_codes
        absent_codes.update(newly_absent)

        if brand_new or returned or newly_absent:
            save_state(seen_snapshots, absent_codes)

        elapsed = time.time() - loop_start
        qsize   = pdp_queue.qsize()
        if new_queued == 0:
            msg = (
                f"[{datetime.now():%H:%M:%S}] "
                f"listing={len(current_codes)} cached={len(cached_codes)} "
                f"absent={len(absent_codes)} pdp_q={qsize} cycle={elapsed:.3f}s"
            )
            print(f"\r{msg:<100}", end="", flush=True)
        else:
            print(f"[{datetime.now():%H:%M:%S}] 📥 {new_queued} queued | listing={len(current_codes)} cached={len(cached_codes)} pdp_q={qsize} cycle={elapsed:.3f}s")

        sleep_remaining = CHECK_INTERVAL - elapsed
        if sleep_remaining > 0:
            time.sleep(sleep_remaining)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Monitor stopped.")
        send_telegram("🛑 SHEIN Monitor stopped.\n@CoBra_SR")
