#!/usr/bin/env python3
"""
SHEIN Monitor - Final Fixed Version
- Shows actual price (not offer price)
- Doesn't alert out of stock
- Clean product ID (no color)
- Clean size format
- Individual product messages
"""

import requests
import json
import time
import sys
import threading
import os
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

def make_session():
    s = requests.Session()
    from requests.adapters import HTTPAdapter
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

session = make_session()

BOT_TOKEN      = "8679800339:AAH1uKwHey2l7tCyl3GPbJW0wzwCzS81I4w"
CHAT_ID        = "1276512925"
CHECK_INTERVAL = 1
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
    "User-Agent": "Android",
    "Client_type": "Android/32",
    "Client_version": "1.0.14",
    "X-Tenant-Id": "SHEIN",
    "Ad_id": "342f47d0-910f-4a29-9bd7-cadb98a2eca9",
    "Accept-Encoding": "gzip, deflate, br",
}

stock_history = {}
stock_history_lock = threading.Lock()
pdp_cache = {}
pdp_cache_lock = threading.Lock()
all_products_map = {}
all_products_lock = threading.Lock()

filter_sizes = set()
filterout_sizes = set()
filter_price_max = None
filter_price_range = None
filter_lock = threading.Lock()

def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        session.post(url, json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=8)
    except:
        pass

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
    except:
        pass

def fetch_pdp_with_curl(color_group: str) -> dict:
    with pdp_cache_lock:
        if color_group in pdp_cache:
            return pdp_cache[color_group]
    
    try:
        pdp_url = f"{PDP_BASE}/{color_group}?storeId=shein&sortOptionsByColor=true&client_type=Android/32&client_version=1.0.14&isNewUser=true&pincode=110001&tagVersionTwo=false&applyExperiment=false&fields=FULL"
        
        cmd = [
            "curl", "-s", "-k",
            "-H", "Host: pdpaggregator-edge.services.sheinindia.in",
            "-H", "Accept: application/json",
            "-H", "User-Agent: Android",
            "-H", "Client_type: Android/32",
            "-H", "Client_version: 1.0.14",
            "-H", "X-Tenant-Id: SHEIN",
            "-H", "Ad_id: 342f47d0-910f-4a29-9bd7-cadb98a2eca9",
            pdp_url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return {}
        
        data = json.loads(result.stdout)
        size_stock = {}
        
        variant_options = data.get("variantOptions", [])
        if not variant_options:
            variant_options = data.get("baseOptions", [])
            if variant_options:
                variant_options = variant_options[0].get("options", [])
        
        for variant in variant_options:
            size = variant.get("scDisplaySize", "")
            if not size:
                for q in variant.get("variantOptionQualifiers", []):
                    val = q.get("value", "")
                    name = q.get("name", "").lower()
                    if val and "color" not in name:
                        size = val
                        break
            
            if not size:
                continue
            
            stock_info = variant.get("stock", {})
            status = stock_info.get("stockLevelStatus", "outOfStock")
            level = int(stock_info.get("stockLevel", 0))
            
            if status in ("inStock", "lowStock") and level > 0:
                size_stock[size] = level
        
        with pdp_cache_lock:
            pdp_cache[color_group] = size_stock
        
        return size_stock
    except:
        return {}

def fetch_listing_page(page: int = 0):
    params = {**LISTING_PARAMS, "currentPage": page}
    try:
        resp = session.get(LISTING_URL, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        products = data.get("products", [])
        total_pages = data.get("pageInfo", {}).get("totalPages", 1)
        return page, products, total_pages
    except:
        return page, None, 0

def fetch_all_listing_products(silent: bool = False):
    try:
        t0 = time.time()
        page, first_products, total_pages = fetch_listing_page(0)
        
        if first_products is None:
            return None
        
        results = {0: first_products}
        
        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=min(total_pages - 1, 5)) as ex:
                futures = {ex.submit(fetch_listing_page, p): p for p in range(1, total_pages)}
                for future in as_completed(futures):
                    pg, prods, _ = future.result()
                    results[pg] = prods or []
        
        all_products = [p for pg in sorted(results) for p in (results[pg] or [])]
        
        if not silent:
            elapsed = time.time() - t0
            print(f"  📋 {len(all_products)} products in {elapsed:.1f}s")
        return all_products
    except:
        return None

def get_option_code(product: dict) -> str:
    """Get colorGroup which is like 443385648_olivegreen"""
    return product.get("fnlColorVariantData", {}).get("colorGroup") or product["code"]

def get_product_id(product: dict) -> str:
    """Extract just the number part like 443385648"""
    code = get_option_code(product)
    return code.split("_")[0]  # Split by underscore and take first part

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
    # Use actual price (not offer price)
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
            resp = requests.post(url, json={"offset": offset, "timeout": 30}, timeout=35)
            updates = resp.json().get("result", [])
            
            for upd in updates:
                offset = upd["update_id"] + 1
                text = upd.get("message", {}).get("text", "").strip()
                
                if not text:
                    continue
                
                if text == "/help":
                    help_msg = """👋 <b>SHEIN Men's Monitor Bot</b>

<b>🤖 Auto-alerts for:</b>
  🚨 New products (in stock)
  🔄 Restocked products
  📈 Stock increases

━━━━━━━━━━━━━━━━━━
<b>📦 Manual Scan:</b>

/check_existing — scan all products
/chex M L 38 — scan by size

━━━━━━━━━━━━━━━━━━
<b>📐 Size Filters:</b>

/filter M L — Alert ONLY these sizes
/filterout 38 — Hide these sizes
/rfilter — clear all filters
/rfilter M — remove only M

━━━━━━━━━━━━━━━━━━
<b>💰 Price Filters:</b>

/filterprice 600 — max price
/flrange 100 300 — price range
/rflprice — clear price

━━━━━━━━━━━━━━━━━━
<b>📊 Status:</b>

/chstat — live stock count
/flstat — active filters

@CoBra_SR"""
                    send_telegram(help_msg)
                
                elif text == "/check_existing":
                    send_telegram("🔍 Scanning all products...")
                    
                    with all_products_lock:
                        total = len(all_products_map)
                        in_stock_count = 0
                        
                        if total > 0:
                            send_telegram(f"📊 Found {total} total products - Checking stock...")
                            
                            for code, product in list(all_products_map.items()):
                                sizes = fetch_pdp_with_curl(code)
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
                                    time.sleep(0.5)  # Small delay to avoid rate limiting
                            
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
                        for code, product in all_products_map.items():
                            sizes = fetch_pdp_with_curl(code)
                            if sizes and (set(sizes.keys()) & size_filter):
                                matches.append((code, product, sizes))
                    
                    if matches:
                        send_telegram(f"✅ <b>Found {len(matches)} products - Sending individually...</b>")
                        
                        # Send each product as an individual message
                        for code, product, sizes in matches:
                            name = product.get("name", code)[:70]  # Slightly longer name
                            actual_price = product.get("price", {})
                            if isinstance(actual_price, dict):
                                price_str = actual_price.get("displayformattedValue", "N/A")
                            else:
                                price_str = f"Rs.{actual_price}"
                            
                            # Get clean product ID
                            product_id = get_product_id(product)
                            
                            # Format sizes for this product
                            size_lines = ""
                            for size in sorted(sizes.keys()):
                                if size in size_filter:  # Only show matching sizes if filter is active
                                    count = sizes[size]
                                    icon = get_stock_icon(count)
                                    size_lines += f"  {icon} {size}: {count} Unit(s)\n"
                            
                            # If no specific filter, show all sizes
                            if not size_lines:
                                for size in sorted(sizes.keys()):
                                    count = sizes[size]
                                    icon = get_stock_icon(count)
                                    size_lines += f"  {icon} {size}: {count} Unit(s)\n"
                            
                            alert = f"""🚨 PRODUCT FOUND

🏷️ {name}
🆔 Product ID: {product_id}

💰 Price: {price_str}

📊 Size Availability:
{size_lines}
🔗 {BASE_URL}/p/{code}

@CoBra_SR"""
                            
                            send_telegram(alert)
                            time.sleep(0.5)  # Small delay to avoid rate limiting
                    else:
                        send_telegram(f"❌ No products found with sizes: {', '.join(size_filter)}")
                
                elif text.startswith("/filter "):
                    sizes = set(text.replace("/filter ", "").split())
                    with filter_lock:
                        filter_sizes.clear()
                        filter_sizes.update(sizes)
                    send_telegram(f"✅ <b>Size Filter Set</b>\n\nAlert ONLY if: <code>{', '.join(sorted(sizes))}</code>")
                
                elif text.startswith("/filterout "):
                    sizes = set(text.replace("/filterout ", "").split())
                    with filter_lock:
                        filterout_sizes.clear()
                        filterout_sizes.update(sizes)
                    send_telegram(f"✅ <b>Exclude Filter Set</b>\n\nHide if only: <code>{', '.join(sorted(sizes))}</code>")
                
                elif text == "/rfilter":
                    with filter_lock:
                        filter_sizes.clear()
                    send_telegram("✅ Size filters cleared")
                
                elif text.startswith("/rfilter "):
                    to_remove = set(text.replace("/rfilter ", "").split())
                    with filter_lock:
                        filter_sizes.difference_update(to_remove)
                    send_telegram(f"✅ Removed: {', '.join(to_remove)}\nRemaining: {', '.join(sorted(filter_sizes)) or 'None'}")
                
                elif text == "/rfilterout":
                    with filter_lock:
                        filterout_sizes.clear()
                    send_telegram("✅ Exclude filters cleared")
                
                elif text.startswith("/rfilterout "):
                    to_remove = set(text.replace("/rfilterout ", "").split())
                    with filter_lock:
                        filterout_sizes.difference_update(to_remove)
                    send_telegram(f"✅ Removed: {', '.join(to_remove)}\nRemaining: {', '.join(sorted(filterout_sizes)) or 'None'}")
                
                elif text.startswith("/filterprice "):
                    try:
                        price = int(text.replace("/filterprice ", ""))
                        with filter_lock:
                            filter_price_max = price
                        send_telegram(f"✅ <b>Price Filter Set</b>\n\nMax price: ₹{price}")
                    except:
                        send_telegram("❌ Use: /filterprice 600")
                
                elif text.startswith("/flrange "):
                    try:
                        parts = text.replace("/flrange ", "").split()
                        min_p, max_p = int(parts[0]), int(parts[1])
                        with filter_lock:
                            filter_price_range = (min_p, max_p)
                        send_telegram(f"✅ <b>Price Range Set</b>\n\n₹{min_p} - ₹{max_p}")
                    except:
                        send_telegram("❌ Use: /flrange 100 300")
                
                elif text == "/rflprice":
                    with filter_lock:
                        filter_price_max = None
                        filter_price_range = None
                    send_telegram("✅ Price filters cleared")
                
                elif text == "/chstat":
                    with all_products_lock:
                        total = len(all_products_map)
                    with stock_history_lock:
                        in_stock = len([s for s in stock_history.values() if s])
                    
                    send_telegram(f"""📊 <b>Live Stock Count</b>

Total Products: {total}
✅ In Stock: {in_stock}
❌ Out of Stock: {total - in_stock}

Size icons:
🔴 1–3 units
🟡 4–10 units
🟢 11+ units""")
                
                elif text == "/flstat":
                    with filter_lock:
                        msg = f"""<b>📋 Active Filters</b>

<b>Size Include:</b> {', '.join(sorted(filter_sizes)) or 'None (alert all)'}
<b>Size Exclude:</b> {', '.join(sorted(filterout_sizes)) or 'None'}
<b>Max Price:</b> ₹{filter_price_max or 'No limit'}
<b>Price Range:</b> {filter_price_range or 'No range'}"""
                    send_telegram(msg)
        
        except:
            pass
        time.sleep(1)

def main():
    print("="*70)
    print("  SHEIN Monitor - Final Version")
    print("  Actual Price | No Out-of-Stock | Clean IDs | Individual Messages")
    print("="*70)
    
    start_health_server()
    seen_codes = load_state()
    
    print("\n📡 Fetching listing…")
    products = fetch_all_listing_products()
    
    if products is None:
        print("❌ Failed. Exiting.")
        sys.exit(1)
    
    with all_products_lock:
        all_products_map.update({get_option_code(p): p for p in products})
    
    current_codes = {get_option_code(p) for p in products}
    new_codes = current_codes - seen_codes
    
    print(f"✅ Baseline: {len(current_codes)} | New: {len(new_codes)}")
    print(f"🔄 Monitoring... (Ctrl+C to stop)\n")
    
    threading.Thread(target=telegram_listener, daemon=True).start()
    send_telegram(f"""👋 <b>SHEIN Monitor Started</b>

👕 Products: {len(current_codes)}
🆕 New: {len(new_codes)}

💬 /help - See all commands
@CoBra_SR""")
    
    while True:
        loop_start = time.time()
        products = fetch_all_listing_products(silent=True)
        
        if products is None:
            time.sleep(CHECK_INTERVAL)
            continue
        
        current_map = {get_option_code(p): p for p in products}
        current_codes = set(current_map.keys())
        
        with all_products_lock:
            all_products_map.update(current_map)
        
        brand_new = current_codes - seen_codes
        
        for code in brand_new:
            product = current_map[code]
            name = product.get("name", code)
            
            print(f"\n[{datetime.now():%H:%M:%S}] 🔍 NEW: {name[:50]}...", end="", flush=True)
            sizes = fetch_pdp_with_curl(code)
            print(f" ✓")
            
            # Don't alert if out of stock
            if not sizes:
                print(f"  ❌ Out of stock - skipped")
                seen_codes.add(code)
                continue
            
            # Check filters
            if not apply_size_filter(sizes) or not apply_price_filter(product):
                print(f"  ⏭️ Filtered")
                seen_codes.add(code)
                continue
            
            with stock_history_lock:
                stock_history[code] = sizes.copy()
            
            # Get ACTUAL price (not offer price)
            actual_price = product.get("price", {})
            if isinstance(actual_price, dict):
                price_str = actual_price.get("displayformattedValue", "N/A")
            else:
                price_str = f"Rs.{actual_price}"
            
            # Get clean product ID (just the number)
            product_id = get_product_id(product)
            
            # Format sizes without (X total)
            size_lines = ""
            for size in sorted(sizes.keys()):
                count = sizes[size]
                icon = get_stock_icon(count)
                size_lines += f"  {icon} {size}: {count} Unit(s)\n"
            
            alert = f"""🚨 NEW PRODUCT [NEW]

🏷️ {name}
🆔 Product ID: {product_id}

💰 Price: {price_str}

📊 Size Availability:
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
              f"cache={len(pdp_cache)} {elapsed:.2f}s       ", end="", flush=True)
        
        sleep_remaining = CHECK_INTERVAL - elapsed
        if sleep_remaining > 0:
            time.sleep(sleep_remaining)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Stopped.")
        send_telegram("🛑 Monitor Stopped\n@CoBra_SR")