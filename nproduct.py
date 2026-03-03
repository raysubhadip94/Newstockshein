#!/usr/bin/env python3
"""
SHEIN Monitor - Final Improved Version
Handles out-of-stock products properly
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

# CONFIG
BOT_TOKEN      = "8679800339:AAH1uKwHey2l7tCyl3GPbJW0wzwCzS81I4w"
CHAT_ID        = "1276512925"
CHECK_INTERVAL = 1
BASE_URL       = "https://sheinindia.in"
STATE_FILE     = "shein_state_final.json"

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

# Track stock history for restock detection
stock_history = {}
stock_history_lock = threading.Lock()

# PDP cache
pdp_cache = {}
pdp_cache_lock = threading.Lock()

# Product status tracking
product_status = {}  # {code: "in_stock" | "out_of_stock"}
product_status_lock = threading.Lock()

# Stats
stats = {
    "listing_calls": 0,
    "pdp_calls": 0,
    "last_check": None,
    "new_products": 0,
    "restocks": 0,
    "out_of_stock": 0,
}
stats_lock = threading.Lock()

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
    """Fetch PDP using curl"""
    with pdp_cache_lock:
        if color_group in pdp_cache:
            return pdp_cache[color_group]
    
    with stats_lock:
        stats["pdp_calls"] += 1
    
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
    
    except Exception as e:
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
        with stats_lock:
            stats["listing_calls"] += 1
        
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
    return product.get("fnlColorVariantData", {}).get("colorGroup") or product["code"]

filter_sizes = set()
filterout_sizes = set()
filter_price_max = None
filter_price_range = None
filter_lock = threading.Lock()

def apply_size_filter(sizes: dict) -> bool:
    with filter_lock:
        if not sizes:
            return False  # No sizes = don't alert (out of stock)
        if filter_sizes and not (set(sizes.keys()) & filter_sizes):
            return False
        if filterout_sizes and (set(sizes.keys()) & filterout_sizes):
            return False
    return True

def apply_price_filter(product: dict) -> bool:
    price = product.get("offerPrice", {}).get("value", 0)
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

def format_new_alert(product: dict, sizes: dict) -> str:
    """Format NEW product alert"""
    code = get_option_code(product)
    name = product.get("name", "Unknown")
    price = product.get("offerPrice", {}).get("displayformattedValue", "N/A")
    
    # Format sizes with counts
    size_lines = ""
    total_units = 0
    if sizes:
        for size in sorted(sizes.keys()):
            count = sizes[size]
            total_units += count
            size_lines += f"  🔴 {size}: {count} Unit(s)\n"
    else:
        # OUT OF STOCK
        return f"""🚨 NEW PRODUCT [OUT OF STOCK]

🏷️ {name}
🆔 Product ID: {code}

💰 Price: {price}

❌ <b>Currently Out of Stock</b>

🔗 {BASE_URL}/p/{code}

@CoBra_SR"""
    
    msg = f"🚨 NEW PRODUCT [NEW]\n\n"
    msg += f"🏷️ {name}\n"
    msg += f"🆔 Product ID: {code}\n\n"
    msg += f"💰 Price: {price}\n\n"
    msg += f"📊 Size Availability ({total_units} total):\n"
    msg += size_lines
    msg += f"🔗 {BASE_URL}/p/{code}\n\n"
    msg += f"@CoBra_SR"
    
    return msg

def format_restock_alert(product: dict, old_sizes: dict, new_sizes: dict) -> str:
    """Format RESTOCK alert with differences"""
    code = get_option_code(product)
    name = product.get("name", "Unknown")
    price = product.get("offerPrice", {}).get("displayformattedValue", "N/A")
    
    # Calculate added stock
    size_lines = ""
    total_added = 0
    
    for size in sorted(set(list(old_sizes.keys()) + list(new_sizes.keys()))):
        old_qty = old_sizes.get(size, 0)
        new_qty = new_sizes.get(size, 0)
        diff = new_qty - old_qty
        
        if diff > 0:
            size_lines += f"  🟢 {size}: {new_qty} Unit(s)  +{diff} added\n"
            total_added += diff
        elif diff < 0:
            size_lines += f"  🟡 {size}: {new_qty} Unit(s)  {diff} removed\n"
        elif new_qty > 0:
            size_lines += f"  ⚪ {size}: {new_qty} Unit(s)  (no change)\n"
    
    msg = f"📈 RESTOCK DETECTED [+{total_added}]\n\n"
    msg += f"🏷️ {name}\n"
    msg += f"🆔 Product ID: {code}\n\n"
    msg += f"💰 Price: {price}\n\n"
    msg += f"📊 Stock Changes:\n"
    msg += size_lines
    msg += f"🔗 {BASE_URL}/p/{code}\n\n"
    msg += f"@CoBra_SR"
    
    return msg

def format_back_in_stock_alert(product: dict, new_sizes: dict) -> str:
    """Format BACK IN STOCK alert"""
    code = get_option_code(product)
    name = product.get("name", "Unknown")
    price = product.get("offerPrice", {}).get("displayformattedValue", "N/A")
    
    size_lines = ""
    total_units = 0
    for size in sorted(new_sizes.keys()):
        count = new_sizes[size]
        total_units += count
        size_lines += f"  🟢 {size}: {count} Unit(s)\n"
    
    msg = f"✅ BACK IN STOCK!\n\n"
    msg += f"🏷️ {name}\n"
    msg += f"🆔 Product ID: {code}\n\n"
    msg += f"💰 Price: {price}\n\n"
    msg += f"📊 Available Sizes ({total_units} total):\n"
    msg += size_lines
    msg += f"🔗 {BASE_URL}/p/{code}\n\n"
    msg += f"@CoBra_SR"
    
    return msg

def telegram_listener():
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
                    help_text = """<b>📋 SHEIN Monitor Commands</b>

<b>Size Filters:</b>
/filter M L - Alert only if M or L in stock
/filterout 38 - Block alerts with only 38
/rfilter - Clear size filters

<b>Price Filters:</b>
/filterprice 600 - Max price ₹600
/flrange 100 300 - Price range ₹100-300
/rflrange - Clear price filters

<b>Status:</b>
/stats - Show monitor statistics
/flstat - Show active filters

<b>Examples:</b>
/filter M L XL - Alert if any of these sizes
/filterprice 500 - Only alert products ≤₹500
"""
                    send_telegram(help_text)
                
                elif text.startswith("/filter "):
                    sizes = set(text.replace("/filter ", "").split())
                    with filter_lock:
                        filter_sizes.clear()
                        filter_sizes.update(sizes)
                    send_telegram(f"✅ <b>Size Filter Set</b>\nAlert only if: <code>{', '.join(sorted(sizes))}</code>")
                
                elif text.startswith("/filterout "):
                    sizes = set(text.replace("/filterout ", "").split())
                    with filter_lock:
                        filterout_sizes.clear()
                        filterout_sizes.update(sizes)
                    send_telegram(f"✅ <b>Exclude Filter Set</b>\nHide alerts with only: <code>{', '.join(sorted(sizes))}</code>")
                
                elif text.startswith("/filterprice "):
                    try:
                        price = int(text.replace("/filterprice ", ""))
                        with filter_lock:
                            filter_price_max = price
                        send_telegram(f"✅ <b>Price Filter Set</b>\nMax price: ₹{price}")
                    except:
                        send_telegram("❌ Invalid price. Use: /filterprice 500")
                
                elif text.startswith("/flrange "):
                    try:
                        parts = text.replace("/flrange ", "").split()
                        min_p, max_p = int(parts[0]), int(parts[1])
                        with filter_lock:
                            filter_price_range = (min_p, max_p)
                        send_telegram(f"✅ <b>Price Range Set</b>\n₹{min_p} - ₹{max_p}")
                    except:
                        send_telegram("❌ Use: /flrange 100 500")
                
                elif text == "/rfilter":
                    with filter_lock:
                        filter_sizes.clear()
                        filterout_sizes.clear()
                    send_telegram("✅ Size filters cleared")
                
                elif text == "/rflrange":
                    with filter_lock:
                        filter_price_range = None
                    send_telegram("✅ Price range cleared")
                
                elif text == "/stats":
                    with stats_lock:
                        msg = f"""📊 <b>Monitor Statistics</b>

📋 Listing calls: <code>{stats['listing_calls']}</code>
🔍 PDP calls: <code>{stats['pdp_calls']}</code>
💾 Cache size: <code>{len(pdp_cache)}</code>
🆕 New products: <code>{stats['new_products']}</code>
📈 Restocks: <code>{stats['restocks']}</code>
❌ Out of stock: <code>{stats['out_of_stock']}</code>
🕐 Last check: <code>{stats['last_check']}</code>"""
                    send_telegram(msg)
                
                elif text == "/flstat":
                    with filter_lock:
                        msg = f"""<b>📋 Active Filters</b>

<b>Size Include:</b> {', '.join(sorted(filter_sizes)) or 'None (alert all in-stock)'}
<b>Size Exclude:</b> {', '.join(sorted(filterout_sizes)) or 'None'}
<b>Max Price:</b> ₹{filter_price_max or 'No limit'}
<b>Price Range:</b> {filter_price_range or 'No range'}"""
                    send_telegram(msg)
        
        except:
            pass
        time.sleep(1)

def main():
    print("="*70)
    print("  SHEIN Monitor - Final Improved Version")
    print("  Proper Out-of-Stock Handling")
    print("="*70)
    
    start_health_server()
    seen_codes = load_state()
    
    print("\n📡 Fetching listing…")
    products = fetch_all_listing_products()
    
    if products is None:
        print("❌ Failed. Exiting.")
        sys.exit(1)
    
    current_codes = {get_option_code(p) for p in products}
    new_codes = current_codes - seen_codes
    
    print(f"✅ Baseline: {len(current_codes)} | New: {len(new_codes)}")
    print(f"🔄 Monitoring... (Ctrl+C to stop)\n")
    
    threading.Thread(target=telegram_listener, daemon=True).start()
    send_telegram(f"""✅ <b>Monitor Started</b>

👕 Products: {len(current_codes)}
🆕 New: {len(new_codes)}
📊 Smart PDP calls only for new products
❌ Out of stock products will be marked

💬 /help - See all commands
@CoBra_SR""")
    
    while True:
        loop_start = time.time()
        products = fetch_all_listing_products(silent=True)
        
        if products is None:
            time.sleep(CHECK_INTERVAL)
            continue
        
        with stats_lock:
            stats["last_check"] = datetime.now().strftime("%H:%M:%S")
        
        current_map = {get_option_code(p): p for p in products}
        current_codes = set(current_map.keys())
        
        brand_new = current_codes - seen_codes
        
        for code in brand_new:
            product = current_map[code]
            name = product.get("name", code)
            
            print(f"\n[{datetime.now():%H:%M:%S}] 🔍 NEW: {name[:50]}...", end="", flush=True)
            sizes = fetch_pdp_with_curl(code)
            print(f" ✓")
            
            # Check if out of stock
            if not sizes:
                with product_status_lock:
                    product_status[code] = "out_of_stock"
                with stats_lock:
                    stats["out_of_stock"] += 1
                
                alert = format_new_alert(product, sizes)
                send_telegram(alert)
                print(f"  ⚠️ OUT OF STOCK - Alert sent!")
                
                with stock_history_lock:
                    stock_history[code] = {}
                
                seen_codes.add(code)
                continue
            
            # Check filters
            if not apply_size_filter(sizes) or not apply_price_filter(product):
                print(f"  ⏭️ Filtered")
                with product_status_lock:
                    product_status[code] = "in_stock"
                seen_codes.add(code)
                continue
            
            # In stock and passes filters
            with product_status_lock:
                product_status[code] = "in_stock"
            with stock_history_lock:
                stock_history[code] = sizes.copy()
            
            # Send alert
            alert = format_new_alert(product, sizes)
            send_telegram(alert)
            
            print(f"  ✅ Alert sent!")
            
            with stats_lock:
                stats["new_products"] += 1
            
            seen_codes.add(code)
        
        # Check for restocks/back in stock
        for code in seen_codes:
            if code in current_map and code in stock_history:
                product = current_map[code]
                new_sizes = fetch_pdp_with_curl(code)
                old_sizes = stock_history[code]
                old_status = product_status.get(code, "unknown")
                
                # Check status change: was out of stock, now in stock
                if old_status == "out_of_stock" and new_sizes:
                    if apply_size_filter(new_sizes) and apply_price_filter(product):
                        alert = format_back_in_stock_alert(product, new_sizes)
                        send_telegram(alert)
                        
                        with product_status_lock:
                            product_status[code] = "in_stock"
                        
                        print(f"\n[{datetime.now():%H:%M:%S}] ✅ Back in stock: {product.get('name', code)[:50]}")
                
                # Check for stock increase in existing in-stock products
                elif old_status == "in_stock" and new_sizes and new_sizes != old_sizes:
                    total_old = sum(old_sizes.values())
                    total_new = sum(new_sizes.values())
                    
                    if total_new > total_old:
                        if apply_size_filter(new_sizes) and apply_price_filter(product):
                            alert = format_restock_alert(product, old_sizes, new_sizes)
                            send_telegram(alert)
                            
                            with stats_lock:
                                stats["restocks"] += 1
                            
                            print(f"\n[{datetime.now():%H:%M:%S}] 📈 Restock: {product.get('name', code)[:50]}")
                
                with stock_history_lock:
                    stock_history[code] = new_sizes.copy()
        
        seen_codes = seen_codes | current_codes
        
        if brand_new:
            save_state(seen_codes)
        
        elapsed = time.time() - loop_start
        
        with stats_lock:
            print(f"\r[{datetime.now():%H:%M:%S}] products={len(current_codes)} new={len(brand_new)} "
                  f"pdp={stats['pdp_calls']} oos={stats['out_of_stock']} {elapsed:.2f}s       ", end="", flush=True)
        
        sleep_remaining = CHECK_INTERVAL - elapsed
        if sleep_remaining > 0:
            time.sleep(sleep_remaining)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Stopped.")
        send_telegram("🛑 <b>Monitor Stopped</b>\n@CoBra_SR")
