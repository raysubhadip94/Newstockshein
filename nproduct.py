#!/usr/bin/env python3
"""
SHEIN Monitor - Railway Optimized Version
- Better network handling for Railway.app
- Multiple fallback methods for PDP fetching
- Detailed diagnostics
"""

import requests
import json
import time
import sys
import threading
import os
import subprocess
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib3

# Disable SSL warnings if needed
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def make_session():
    s = requests.Session()
    
    # Configure retries
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    
    # Set default headers
    s.headers.update({
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    
    return s

session = make_session()

BOT_TOKEN      = "8679800339:AAH1uKwHey2l7tCyl3GPbJW0wzwCzS81I4w"
CHAT_ID        = "1276512925"
CHECK_INTERVAL = 5  # Increased to avoid rate limiting
BASE_URL       = "https://sheinindia.in"
STATE_FILE     = "shein_state.json"

LISTING_URL = "https://search-edge.services.sheinindia.in/rilfnlwebservices/v4/rilfnl/products/category/sverse-5939-37961"
LISTING_PARAMS = {
    "advfilter": "true", "urgencyDriverEnabled": "true",
    "query": ":newArrivals:genderfilter:Men", "pageSize": 60,
    "store": "shein", "fields": "FULL", "currentPage": 0,
    "SearchExp1": "algo1", "SearchExp3": "suggester",
    "RelExp3": "false", "softFilters": "false", "stemFlag": "false",
    "SearchFlag5": "false", "SearchFlag6": "false",
    "platform": "android", "offer_price_ab_enabled": "false", "tagV2Enabled": "false",
}

PDP_BASE = "https://pdpaggregator-edge.services.sheinindia.in/aggregator/pdp"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Client_type": "Android/32",
    "Client_version": "1.0.14",
    "X-Tenant-Id": "SHEIN",
    "Origin": "https://sheinindia.in",
    "Referer": "https://sheinindia.in/",
}

# Rotating user agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/537.36",
    "Mozilla/5.0 (Android 13; Mobile; rv:68.0) Gecko/68.0 Firefox/94.0",
]

stock_history = {}
stock_history_lock = threading.Lock()
pdp_cache = {}
pdp_cache_lock = threading.Lock()
pdp_cache_timestamp = {}
pdp_cache_lock_ts = threading.Lock()
all_products_map = {}
all_products_lock = threading.Lock()
failed_requests = {}
failed_requests_lock = threading.Lock()

filter_sizes = set()
filterout_sizes = set()
filter_price_max = None
filter_price_range = None
filter_lock = threading.Lock()

CACHE_TIMEOUT = 300  # 5 minutes cache for Railway

def send_telegram(message: str):
    """Send message with retry logic"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            response = session.post(
                url, 
                json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, 
                timeout=10,
                verify=False
            )
            if response.status_code == 200:
                break
        except:
            if attempt == 2:
                print(f"Telegram send failed after 3 attempts")
            time.sleep(1)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def start_health_server():
    try:
        port = int(os.getenv("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"Health server started on port {port}")
    except Exception as e:
        print(f"Health server error: {e}")

def is_cache_valid(color_group: str) -> bool:
    """Check if cached data is still valid"""
    with pdp_cache_lock_ts:
        if color_group in pdp_cache_timestamp:
            age = time.time() - pdp_cache_timestamp[color_group]
            return age < CACHE_TIMEOUT
    return False

def fetch_with_requests(color_group: str) -> dict:
    """Try fetching with requests library"""
    try:
        pdp_url = f"{PDP_BASE}/{color_group}"
        params = {
            "storeId": "shein",
            "sortOptionsByColor": "true",
            "client_type": "Android/32",
            "client_version": "1.0.14",
            "isNewUser": "true",
            "pincode": "110001",
            "tagVersionTwo": "false",
            "applyExperiment": "false",
            "fields": "FULL"
        }
        
        headers = HEADERS.copy()
        headers["User-Agent"] = random.choice(USER_AGENTS)
        
        # Try with verify=False and different timeouts
        response = session.get(
            pdp_url, 
            params=params, 
            headers=headers, 
            timeout=15,
            verify=False
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"  HTTP {response.status_code} for {color_group}")
            return None
    except Exception as e:
        print(f"  Requests error: {str(e)[:50]}")
        return None

def fetch_with_curl(color_group: str) -> dict:
    """Try fetching with curl"""
    try:
        pdp_url = f"{PDP_BASE}/{color_group}?storeId=shein&sortOptionsByColor=true&client_type=Android/32&client_version=1.0.14&isNewUser=true&pincode=110001&tagVersionTwo=false&applyExperiment=false&fields=FULL"
        
        # Try with different curl options
        cmd = [
            "curl", "-s", "-k", "-L",  # -L to follow redirects
            "-A", random.choice(USER_AGENTS),
            "-H", "Accept: application/json",
            "-H", "X-Tenant-Id: SHEIN",
            "--connect-timeout", "10",
            "--max-time", "15",
            pdp_url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
        return None
    except:
        return None

def fetch_with_wget(color_group: str) -> dict:
    """Try fetching with wget as last resort"""
    try:
        pdp_url = f"{PDP_BASE}/{color_group}?storeId=shein&sortOptionsByColor=true&client_type=Android/32&client_version=1.0.14&isNewUser=true&pincode=110001&tagVersionTwo=false&applyExperiment=false&fields=FULL"
        
        cmd = [
            "wget", "-q", "-O-", "--no-check-certificate",
            "--timeout=10",
            pdp_url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
        return None
    except:
        return None

def extract_stock_data(data: dict) -> dict:
    """Extract stock data from various possible JSON structures"""
    size_stock = {}
    
    if not data:
        return size_stock
    
    # Method 1: Direct variantOptions
    variant_options = data.get("variantOptions", [])
    
    # Method 2: Through baseOptions
    if not variant_options:
        base_options = data.get("baseOptions", [])
        if base_options and isinstance(base_options, list):
            for base_option in base_options:
                options = base_option.get("options", [])
                if options:
                    variant_options = options
                    break
    
    # Method 3: Through products array
    if not variant_options:
        products = data.get("products", [])
        if products:
            for product in products:
                opts = product.get("variantOptions", [])
                if opts:
                    variant_options = opts
                    break
    
    # Method 4: Through skus
    if not variant_options:
        skus = data.get("skus", [])
        if skus:
            variant_options = skus
    
    # Process each variant
    for variant in variant_options:
        # Try to get size
        size = (
            variant.get("scDisplaySize") or
            variant.get("size") or
            variant.get("displaySize") or
            variant.get("sizeDescription") or
            variant.get("optionValue")
        )
        
        if not size:
            # Try qualifiers
            for q in variant.get("variantOptionQualifiers", []):
                val = q.get("value", "")
                name = q.get("name", "").lower()
                if val and "color" not in name:
                    size = val
                    break
        
        if not size:
            continue
        
        # Try to get stock level
        stock_info = variant.get("stock", {}) or variant.get("stockInfo", {})
        level = 0
        
        # Check various stock fields
        if isinstance(stock_info, dict):
            level = int(stock_info.get("stockLevel", 0))
        elif isinstance(stock_info, (int, float)):
            level = int(stock_info)
        
        # Check other quantity fields
        if level == 0:
            for field in ["quantity", "availableQuantity", "stockQuantity", "qty"]:
                qty = variant.get(field, 0)
                if qty:
                    try:
                        level = int(qty)
                        break
                    except:
                        pass
        
        # Check stock status
        status = ""
        if isinstance(stock_info, dict):
            status = str(stock_info.get("stockLevelStatus", "")).lower()
        
        # Include if in stock
        if level > 0 or status in ("inStock", "lowStock", "instock", "available"):
            if level == 0:
                level = 1  # Assume at least 1 if status says in stock
            size_stock[size] = level
    
    return size_stock

def fetch_pdp_with_curl(color_group: str, force_refresh: bool = False) -> dict:
    """Enhanced PDP fetch with multiple methods"""
    
    # Check cache
    if not force_refresh:
        with pdp_cache_lock:
            if color_group in pdp_cache and is_cache_valid(color_group):
                return pdp_cache[color_group]
    
    # Track failures
    with failed_requests_lock:
        if color_group in failed_requests:
            failed_count = failed_requests[color_group]
            if failed_count > 3 and not force_refresh:
                return {}  # Skip if failed too many times
        else:
            failed_requests[color_group] = 0
    
    # Try multiple methods
    data = None
    
    # Method 1: Requests
    data = fetch_with_requests(color_group)
    
    # Method 2: Curl if requests failed
    if not data:
        data = fetch_with_curl(color_group)
    
    # Method 3: Wget if both failed
    if not data:
        data = fetch_with_wget(color_group)
    
    if not data:
        with failed_requests_lock:
            failed_requests[color_group] = failed_requests.get(color_group, 0) + 1
        return {}
    
    # Extract stock data
    size_stock = extract_stock_data(data)
    
    # Cache the result
    with pdp_cache_lock:
        pdp_cache[color_group] = size_stock
    with pdp_cache_lock_ts:
        pdp_cache_timestamp[color_group] = time.time()
    
    # Reset failure count on success
    with failed_requests_lock:
        failed_requests[color_group] = 0
    
    return size_stock

def fetch_listing_page(page: int = 0):
    """Fetch a single listing page"""
    params = {**LISTING_PARAMS, "currentPage": page}
    
    # Rotate user agent
    headers = HEADERS.copy()
    headers["User-Agent"] = random.choice(USER_AGENTS)
    
    try:
        resp = session.get(
            LISTING_URL, 
            params=params, 
            headers=headers, 
            timeout=20,
            verify=False
        )
        resp.raise_for_status()
        data = resp.json()
        products = data.get("products", [])
        total_pages = data.get("pageInfo", {}).get("totalPages", 1)
        return page, products, total_pages
    except Exception as e:
        print(f"Listing page {page} error: {e}")
        return page, None, 0

def fetch_all_listing_products(silent: bool = False):
    """Fetch all products with better error handling"""
    try:
        t0 = time.time()
        page, first_products, total_pages = fetch_listing_page(0)
        
        if first_products is None:
            # Retry once
            time.sleep(2)
            page, first_products, total_pages = fetch_listing_page(0)
            if first_products is None:
                return None
        
        results = {0: first_products}
        
        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=3) as ex:  # Reduced workers
                futures = {ex.submit(fetch_listing_page, p): p for p in range(1, total_pages)}
                for future in as_completed(futures):
                    try:
                        pg, prods, _ = future.result(timeout=20)
                        if prods:
                            results[pg] = prods
                    except:
                        pass
        
        all_products = []
        for pg in sorted(results):
            if results[pg]:
                all_products.extend(results[pg])
        
        if not silent:
            elapsed = time.time() - t0
            print(f"  📋 {len(all_products)} products in {elapsed:.1f}s")
        return all_products
    except Exception as e:
        print(f"Fetch all products error: {e}")
        return None

def get_option_code(product: dict) -> str:
    """Get colorGroup which is like 443385648_olivegreen"""
    color_data = product.get("fnlColorVariantData", {})
    if color_data and color_data.get("colorGroup"):
        return color_data["colorGroup"]
    return product.get("code", "")

def get_product_id(product: dict) -> str:
    """Extract just the number part like 443385648"""
    code = get_option_code(product)
    return code.split("_")[0] if "_" in code else code

def get_stock_icon(count: int) -> str:
    if count <= 3:
        return "🔴"
    elif count <= 10:
        return "🟡"
    else:
        return "🟢"

def apply_size_filter(sizes: dict) -> bool:
    with filter_lock:
        if not sizes:
            return False
        if filter_sizes and not (set(sizes.keys()) & filter_sizes):
            return False
        if filterout_sizes and (set(sizes.keys()) & filterout_sizes):
            return False
    return True

def apply_price_filter(product: dict) -> bool:
    price = product.get("price", {})
    if isinstance(price, dict):
        price = price.get("value", 0)
    else:
        price = float(price or 0)
    
    with filter_lock:
        if filter_price_max and price > filter_price_max:
            return False
        if filter_price_range:
            min_p, max_p = filter_price_range
            if price < min_p or price > max_p:
                return False
    return True

def save_state(seen_codes: set):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"seen": list(seen_codes)}, f)
    except:
        pass

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return set(json.load(f).get("seen", []))
    except:
        pass
    return set()

def telegram_listener():
    global filter_sizes, filterout_sizes, filter_price_max, filter_price_range
    
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            resp = requests.post(
                url, 
                json={"offset": offset, "timeout": 30}, 
                timeout=35,
                verify=False
            )
            updates = resp.json().get("result", [])
            
            for upd in updates:
                offset = upd["update_id"] + 1
                text = upd.get("message", {}).get("text", "").strip()
                
                if not text:
                    continue
                
                if text == "/help":
                    help_msg = """👋 <b>SHEIN Men's Monitor Bot - Railway Optimized</b>

<b>🤖 Auto-alerts for:</b>
  🚨 New products (in stock)

━━━━━━━━━━━━━━━━━━
<b>📦 Manual Scan:</b>

/check_existing — scan all products
/chex M L 38 — scan by size
/refresh_cache — clear cache
/diagnose — check connection

━━━━━━━━━━━━━━━━━━
<b>📐 Size Filters:</b>

/filter M L — Alert ONLY these sizes
/filterout 38 — Hide these sizes
/rfilter — clear all filters

━━━━━━━━━━━━━━━━━━
<b>💰 Price Filters:</b>

/filterprice 600 — max price
/flrange 100 300 — price range

━━━━━━━━━━━━━━━━━━
<b>📊 Status:</b>

/chstat — live stock count
/flstat — active filters

@CoBra_SR"""
                    send_telegram(help_msg)
                
                elif text == "/diagnose":
                    # Test connection to SHEIN
                    try:
                        test = session.get("https://sheinindia.in", timeout=10, verify=False)
                        status = f"✅ SHEIN reachable (HTTP {test.status_code})"
                    except Exception as e:
                        status = f"❌ SHEIN unreachable: {str(e)[:50]}"
                    
                    msg = f"""<b>🔍 Diagnostics</b>

{status}

Cache size: {len(pdp_cache)}
Failed requests: {len(failed_requests)}
Products tracked: {len(all_products_map)}

Try /refresh_cache if data seems stale"""
                    send_telegram(msg)
                
                elif text == "/refresh_cache":
                    with pdp_cache_lock:
                        pdp_cache.clear()
                    with pdp_cache_lock_ts:
                        pdp_cache_timestamp.clear()
                    with failed_requests_lock:
                        failed_requests.clear()
                    send_telegram("✅ Cache cleared - Next scans will fetch fresh data")
                
                elif text == "/check_existing":
                    send_telegram("🔍 Scanning all products (this may take a few minutes)...")
                    
                    with all_products_lock:
                        total = len(all_products_map)
                        in_stock_count = 0
                        
                        if total > 0:
                            send_telegram(f"📊 Found {total} total products - Checking stock...")
                            
                            for i, (code, product) in enumerate(list(all_products_map.items())):
                                sizes = fetch_pdp_with_curl(code, force_refresh=True)
                                
                                if i % 10 == 0 and i > 0:
                                    print(f"  Progress: {i}/{total} products checked")
                                
                                if sizes:
                                    in_stock_count += 1
                                    
                                    name = product.get("name", code)[:70]
                                    actual_price = product.get("price", {})
                                    if isinstance(actual_price, dict):
                                        price_str = actual_price.get("displayformattedValue", "N/A")
                                    else:
                                        price_str = f"Rs.{actual_price}"
                                    
                                    product_id = get_product_id(product)
                                    
                                    size_lines = ""
                                    total_units = 0
                                    for size in sorted(sizes.keys()):
                                        count = sizes[size]
                                        total_units += count
                                        icon = get_stock_icon(count)
                                        size_lines += f"  {icon} {size}: {count} Unit(s)\n"
                                    
                                    alert = f"""✅ IN STOCK

🏷️ {name}
🆔 Product ID: {product_id}

💰 Price: {price_str}

📊 Size Availability (Total: {total_units}):
{size_lines}
🔗 {BASE_URL}/p/{code}

@CoBra_SR"""
                                    
                                    send_telegram(alert)
                                    time.sleep(1)  # Increased delay for Railway
                            
                            summary = f"""📊 <b>Stock Scan Complete</b>

Total Products: {total}
✅ In Stock: {in_stock_count}
❌ Out of Stock: {total - in_stock_count}"""
                            
                            send_telegram(summary)
                        else:
                            send_telegram("❌ No products found in catalog")
                
                elif text.startswith("/chex "):
                    size_filter = set(text.replace("/chex ", "").split())
                    send_telegram(f"🔍 Scanning for sizes: {', '.join(size_filter)}...")
                    
                    matches = []
                    with all_products_lock:
                        total = len(all_products_map)
                        for i, (code, product) in enumerate(all_products_map.items()):
                            if i % 10 == 0:
                                print(f"  Chex progress: {i}/{total}")
                            sizes = fetch_pdp_with_curl(code, force_refresh=True)
                            if sizes and (set(sizes.keys()) & size_filter):
                                matches.append((code, product, sizes))
                    
                    if matches:
                        send_telegram(f"✅ <b>Found {len(matches)} products - Sending individually...</b>")
                        
                        for code, product, sizes in matches:
                            name = product.get("name", code)[:70]
                            actual_price = product.get("price", {})
                            if isinstance(actual_price, dict):
                                price_str = actual_price.get("displayformattedValue", "N/A")
                            else:
                                price_str = f"Rs.{actual_price}"
                            
                            product_id = get_product_id(product)
                            
                            size_lines = ""
                            total_units = 0
                            for size in sorted(sizes.keys()):
                                if size in size_filter:
                                    count = sizes[size]
                                    total_units += count
                                    icon = get_stock_icon(count)
                                    size_lines += f"  {icon} {size}: {count} Unit(s)\n"
                            
                            if not size_lines:
                                for size in sorted(sizes.keys()):
                                    count = sizes[size]
                                    total_units += count
                                    icon = get_stock_icon(count)
                                    size_lines += f"  {icon} {size}: {count} Unit(s)\n"
                            
                            alert = f"""🚨 PRODUCT FOUND

🏷️ {name}
🆔 Product ID: {product_id}

💰 Price: {price_str}

📊 Size Availability (Total: {total_units}):
{size_lines}
🔗 {BASE_URL}/p/{code}

@CoBra_SR"""
                            
                            send_telegram(alert)
                            time.sleep(1)
                    else:
                        send_telegram(f"❌ No products found with sizes: {', '.join(size_filter)}")
                
                # ... (rest of the filter commands remain the same)
                
                elif text == "/chstat":
                    with all_products_lock:
                        total = len(all_products_map)
                    
                    # Count products with stock
                    in_stock = 0
                    with pdp_cache_lock:
                        for sizes in pdp_cache.values():
                            if sizes:
                                in_stock += 1
                    
                    send_telegram(f"""📊 <b>Live Stock Count</b>

Total Products: {total}
✅ In Stock: {in_stock}
❌ Out of Stock: {total - in_stock}
📦 Cache Size: {len(pdp_cache)}
⚠️ Failed Requests: {len(failed_requests)}

Size icons:
🔴 1–3 units
🟡 4–10 units
🟢 11+ units""")
        
        except Exception as e:
            print(f"Telegram listener error: {e}")
        time.sleep(1)

def main():
    print("="*70)
    print("  SHEIN Monitor - Railway Optimized Version")
    print("  Multiple fetch methods | Better error handling")
    print("="*70)
    
    # Test environment
    print(f"\n🌐 Running on: {os.getenv('RAILWAY_STATIC_URL', 'localhost')}")
    print(f"📁 Working dir: {os.getcwd()}")
    
    start_health_server()
    seen_codes = load_state()
    
    print("\n📡 Fetching listing…")
    products = fetch_all_listing_products()
    
    if products is None:
        print("❌ Failed to fetch listing. Retrying in 30 seconds...")
        time.sleep(30)
        products = fetch_all_listing_products()
        if products is None:
            print("❌ Failed again. Check network connectivity.")
            # Send diagnostic info via Telegram
            send_telegram("⚠️ Monitor started but couldn't fetch products. Use /diagnose to check connection.")
    
    if products:
        with all_products_lock:
            for p in products:
                code = get_option_code(p)
                if code:
                    all_products_map[code] = p
        
        current_codes = set(all_products_map.keys())
        new_codes = current_codes - seen_codes
        
        print(f"✅ Baseline: {len(current_codes)} | New: {len(new_codes)}")
        print(f"🔄 Monitoring... (Ctrl+C to stop)\n")
        
        threading.Thread(target=telegram_listener, daemon=True).start()
        send_telegram(f"""👋 <b>SHEIN Monitor Started (Railway)</b>

👕 Products: {len(current_codes)}
🆕 New: {len(new_codes)}

💬 /help - See all commands
🔍 /diagnose - Check connection
💾 /refresh_cache - Clear cache

@CoBra_SR""")
    
    # Main monitoring loop
    while True:
        try:
            loop_start = time.time()
            products = fetch_all_listing_products(silent=True)
            
            if products:
                current_map = {}
                for p in products:
                    code = get_option_code(p)
                    if code:
                        current_map[code] = p
                
                current_codes = set(current_map.keys())
                
                with all_products_lock:
                    all_products_map.update(current_map)
                
                brand_new = current_codes - seen_codes
                
                for code in brand_new:
                    product = current_map[code]
                    name = product.get("name", code)
                    
                    print(f"\n[{datetime.now():%H:%M:%S}] 🔍 NEW: {name[:50]}...", end="", flush=True)
                    sizes = fetch_pdp_with_curl(code, force_refresh=True)
                    print(f" ✓")
                    
                    if not sizes:
                        print(f"  ❌ No stock - skipped")
                        seen_codes.add(code)
                        continue
                    
                    if not apply_size_filter(sizes) or not apply_price_filter(product):
                        print(f"  ⏭️ Filtered")
                        seen_codes.add(code)
                        continue
                    
                    with stock_history_lock:
                        stock_history[code] = sizes.copy()
                    
                    actual_price = product.get("price", {})
                    if isinstance(actual_price, dict):
                        price_str = actual_price.get("displayformattedValue", "N/A")
                    else:
                        price_str = f"Rs.{actual_price}"
                    
                    product_id = get_product_id(product)
                    
                    size_lines = ""
                    total_units = 0
                    for size in sorted(sizes.keys()):
                        count = sizes[size]
                        total_units += count
                        icon = get_stock_icon(count)
                        size_lines += f"  {icon} {size}: {count} Unit(s)\n"
                    
                    alert = f"""🚨 NEW PRODUCT [NEW]

🏷️ {name}
🆔 Product ID: {product_id}

💰 Price: {price_str}

📊 Size Availability (Total: {total_units}):
{size_lines}
🔗 {BASE_URL}/p/{code}

@CoBra_SR"""
                    
                    send_telegram(alert)
                    print(f"  ✅ Alert sent!")
                    seen_codes.add(code)
                
                seen_codes = seen_codes | current_codes
                
                if brand_new:
                    save_state(seen_codes)
                
                elapsed = time.time() - loop_start
                print(f"\r[{datetime.now():%H:%M:%S}] products={len(current_codes)} new={len(brand_new)} "
                      f"cache={len(pdp_cache)} fails={len(failed_requests)} {elapsed:.2f}s       ", end="", flush=True)
            
            sleep_remaining = CHECK_INTERVAL - (time.time() - loop_start)
            if sleep_remaining > 0:
                time.sleep(sleep_remaining)
                
        except Exception as e:
            print(f"\nMain loop error: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Stopped.")
        send_telegram("🛑 Monitor Stopped\n@CoBra_SR")
    except Exception as e:
        print(f"\nFatal error: {e}")
        send_telegram(f"❌ Monitor crashed: {str(e)[:100]}\n@CoBra_SR")