import os
import json
import time
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
COOLDOWNS_FILE = os.path.join(DATA_DIR, "quota_cooldowns.json")

_lock = threading.Lock()

# Durations in seconds
COOLDOWN_24H = 24 * 3600       # 86,400s: 20 RPD daily quota limit reached
COOLDOWN_7D = 7 * 24 * 3600     # 604,800s: Deprecated/404 model blacklist
COOLDOWN_BURST = 60            # 60s: Transient RPM rate limit burst
COOLDOWN_SERVER_ERR = 120      # 120s: 503/timeout server recovery

def _load_cooldowns() -> dict:
    if not os.path.exists(COOLDOWNS_FILE):
        return {}
    try:
        with open(COOLDOWNS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cooldowns(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_file = f"{COOLDOWNS_FILE}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, COOLDOWNS_FILE)
    except Exception as e:
        print(f"[QuotaManager] Error saving cooldowns: {e}", flush=True)

def is_daily_quota_error(error_text: str) -> bool:
    """Detects if an error string represents a daily quota limit exhaustion (e.g. 20 RPD)."""
    if not error_text:
        return False
    lowered = error_text.lower()
    daily_signals = [
        "exceeded your current quota",
        "quota exceeded",
        "resource_exhausted",
        "requests per day",
        "perday",
        "daily limit",
        "limit: 20",
        "billing details",
        "free tier limit",
        "quota_exceeded",
    ]
    return any(sig in lowered for sig in daily_signals)

def format_remaining_time(seconds: float) -> str:
    """Returns human readable duration string (e.g. '23.8h', '4.5m', '42s')."""
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    elif seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{max(1, int(seconds))}s"

def is_model_on_cooldown(provider: str, model: str) -> tuple[bool, float, str]:
    """
    Checks if a provider/model is currently on cooldown.
    Returns: (is_cooling_down, remaining_seconds, reason)
    """
    key = f"{provider}:{model}"
    with _lock:
        data = _load_cooldowns()
        entry = data.get(key)
        if not entry:
            return False, 0.0, ""

        now = time.time()
        cooldown_until = entry.get("cooldown_until", 0.0)
        remaining = cooldown_until - now

        if remaining > 0:
            return True, remaining, entry.get("reason", "Active cooldown")

        # Expired: clean up record
        try:
            del data[key]
            _save_cooldowns(data)
        except Exception:
            pass
        return False, 0.0, ""

def record_quota_failure(
    provider: str,
    model: str,
    status_code: int = 429,
    error_text: str = "",
) -> float:
    """
    Registers a failure and sets appropriate cooldown (24 hours for daily quota, 7 days for 404, 60s for RPM).
    Returns timestamp when cooldown expires.
    """
    key = f"{provider}:{model}"
    now = time.time()

    if status_code == 404:
        duration = COOLDOWN_7D
        reason = f"404: Model Deprecated or Not Found (7d blacklist)"
    elif status_code == 429 and is_daily_quota_error(error_text):
        duration = COOLDOWN_24H
        reason = f"429: Daily Quota Exceeded (24h cooldown)"
    elif status_code == 429:
        duration = COOLDOWN_BURST
        reason = f"429: RPM Rate Limit Burst (60s cooldown)"
    elif status_code in (500, 502, 503, 504) or "timeout" in error_text.lower():
        duration = COOLDOWN_SERVER_ERR
        reason = f"{status_code}: Transient Server Overload / Timeout (120s cooldown)"
    else:
        duration = COOLDOWN_BURST
        reason = f"Status {status_code} Error (60s cooldown)"

    cooldown_until = now + duration

    with _lock:
        data = _load_cooldowns()
        data[key] = {
            "provider": provider,
            "model": model,
            "cooldown_until": cooldown_until,
            "duration_seconds": duration,
            "reason": reason,
            "failed_at": now,
            "last_status": status_code,
            "error_snippet": error_text[:200] if error_text else "",
        }
        _save_cooldowns(data)

    rem_str = format_remaining_time(duration)
    print(
        f"[QuotaManager] ⛔ Model '{key}' placed on {rem_str} cooldown. Reason: {reason}",
        flush=True,
    )
    return cooldown_until

def record_success(provider: str, model: str):
    """Clears cooldown records when a provider/model call succeeds."""
    key = f"{provider}:{model}"
    with _lock:
        data = _load_cooldowns()
        if key in data:
            del data[key]
            _save_cooldowns(data)

def get_all_cooldowns() -> dict:
    """Returns currently active cooldowns with formatted remaining time."""
    with _lock:
        data = _load_cooldowns()
        now = time.time()
        active = {}
        for key, entry in list(data.items()):
            rem = entry.get("cooldown_until", 0.0) - now
            if rem > 0:
                active[key] = {
                    **entry,
                    "remaining_seconds": round(rem, 1),
                    "remaining_human": format_remaining_time(rem),
                }
        return active

def reset_all_cooldowns():
    """Wipes all active cooldowns."""
    with _lock:
        _save_cooldowns({})
        print("[QuotaManager] 🔄 All model cooldowns have been reset.", flush=True)

if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv:
        reset_all_cooldowns()
    else:
        active = get_all_cooldowns()
        print(f"Active Quota Cooldowns ({len(active)}):")
        for k, v in active.items():
            print(f"  • {k}: {v['remaining_human']} left ({v['reason']})")
