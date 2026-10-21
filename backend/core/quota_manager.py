import os
import json
import time
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
COOLDOWNS_FILE = os.path.join(DATA_DIR, "quota_cooldowns.json")
USAGE_FILE = os.path.join(DATA_DIR, "model_usage.json")

_lock = threading.Lock()

# Durations in seconds
COOLDOWN_24H = 24 * 3600       # 86,400s: 20 RPD daily quota limit reached
COOLDOWN_7D = 7 * 24 * 3600     # 604,800s: Deprecated/404 model blacklist
COOLDOWN_BURST = 60            # 60s: Transient RPM rate limit burst
COOLDOWN_SERVER_ERR = 120      # 120s: 503/timeout server recovery

MODEL_SPECS = {
    "gemini:gemini-3.1-pro-preview": {
        "name": "Gemini 3.1 Pro Preview",
        "daily_limit": 20,
        "rpm": 2,
        "role": "heavy_coder",
        "vision": True,
    },
    "gemini:gemini-pro-latest": {
        "name": "Gemini Pro Flagship",
        "daily_limit": 20,
        "rpm": 2,
        "role": "heavy_reasoning",
        "vision": True,
    },
    "gemini:gemini-3.5-flash": {
        "name": "Gemini 3.5 Flash",
        "daily_limit": 1500,
        "rpm": 15,
        "role": "chat_vision",
        "vision": True,
    },
    "gemini:gemini-3.5-flash-lite": {
        "name": "Gemini 3.5 Flash-Lite",
        "daily_limit": 1500,
        "rpm": 30,
        "role": "router_chat",
        "vision": True,
    },
    "gemini:gemini-3.1-flash-lite": {
        "name": "Gemini 3.1 Flash-Lite",
        "daily_limit": 1500,
        "rpm": 30,
        "role": "router_fallback",
        "vision": True,
    },
    "gemini:gemini-2.5-flash": {
        "name": "Gemini 2.5 Flash",
        "daily_limit": 1500,
        "rpm": 15,
        "role": "vision_fallback",
        "vision": True,
    },
    "groq:qwen/qwen3.8-27b": {
        "name": "Qwen 3.8 27B (Groq LPU)",
        "daily_limit": 14400,
        "rpm": 30,
        "role": "fast_router_coder",
        "vision": False,
    },
    "groq:openai/gpt-oss-120b": {
        "name": "GPT-OSS 120B (Groq LPU)",
        "daily_limit": 14400,
        "rpm": 30,
        "role": "heavy_coder",
        "vision": False,
    },
    "mistral:codestral-latest": {
        "name": "Codestral Latest (Mistral)",
        "daily_limit": 86400,
        "rpm": 60,
        "role": "code_synthesis",
        "vision": False,
    },
    "mistral:pixtral-12b-2409": {
        "name": "Pixtral 12B (Mistral Vision)",
        "daily_limit": 86400,
        "rpm": 60,
        "role": "multimodal_vision",
        "vision": True,
    },
    "openrouter:google/gemma-3-27b-it": {
        "name": "Google Gemma 3 27B (Vision)",
        "daily_limit": 5000,
        "rpm": 20,
        "role": "multimodal_vision",
        "vision": True,
    },
    "openrouter:google/gemma-3-12b-it": {
        "name": "Google Gemma 3 12B (Vision)",
        "daily_limit": 5000,
        "rpm": 20,
        "role": "multimodal_vision",
        "vision": True,
    },
}

def _get_today_str() -> str:
    return time.strftime("%Y-%m-%d")

def _load_usage() -> dict:
    if not os.path.exists(USAGE_FILE):
        return {"date": _get_today_str(), "counts": {}, "last_used": {}}
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"date": _get_today_str(), "counts": {}, "last_used": {}}
            if data.get("date") != _get_today_str():
                # Rollover day: reset counts for new date
                data["date"] = _get_today_str()
                data["counts"] = {}
            return data
    except Exception:
        return {"date": _get_today_str(), "counts": {}, "last_used": {}}

def _save_usage(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_file = f"{USAGE_FILE}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, USAGE_FILE)
    except Exception as e:
        print(f"[QuotaManager] Error saving usage: {e}", flush=True)

def record_model_call(provider: str, model: str, role: str = "general"):
    """Tracks model invocation for daily usage and identifies active models."""
    key = f"{provider}:{model}"
    with _lock:
        usage = _load_usage()
        counts = usage.setdefault("counts", {})
        counts[key] = counts.get(key, 0) + 1
        last = usage.setdefault("last_used", {})
        last[role] = key
        last["global"] = key
        _save_usage(usage)

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

def get_model_operational_summary() -> dict:
    """
    Returns complete live operational knowledge:
    - Active / last used model
    - Models on cooldown / out of quota
    - Healthy models with daily limits and remaining requests
    """
    usage = _load_usage()
    counts = usage.get("counts", {})
    last_used = usage.get("last_used", {})
    cooldowns = get_all_cooldowns()

    cooling_models = []
    healthy_models = []

    for key, spec in MODEL_SPECS.items():
        daily_limit = spec["daily_limit"]
        used_today = counts.get(key, 0)
        cd_info = cooldowns.get(key)
        is_cooling = cd_info is not None

        if is_cooling:
            rem_human = cd_info["remaining_human"]
            reason = cd_info["reason"]
            cooling_models.append({
                "key": key,
                "name": spec["name"],
                "remaining_requests": 0,
                "cooldown_remaining": rem_human,
                "reason": reason,
                "vision": spec.get("vision", False),
            })
        else:
            remaining_req = max(0, daily_limit - used_today)
            healthy_models.append({
                "key": key,
                "name": spec["name"],
                "used_today": used_today,
                "daily_limit": daily_limit,
                "remaining_requests": remaining_req,
                "rpm": spec["rpm"],
                "role": spec["role"],
                "vision": spec.get("vision", False),
            })

    active_global = last_used.get("global", "groq:qwen/qwen3.8-27b")

    return {
        "active_model": active_global,
        "last_used_by_role": last_used,
        "cooling_models": cooling_models,
        "healthy_models": healthy_models,
    }

def format_model_operational_context() -> str:
    """
    Returns a high-density, authoritative context block explaining
    what model is currently active, what models are out of quota,
    and how many requests are remaining for each model.
    """
    summary = get_model_operational_summary()
    active_m = summary["active_model"]

    lines = [
        "AI ENGINE & MODEL QUOTA STATUS (Ground Truth):",
        f"• Current Active Engine: {active_m} (Router: {summary['last_used_by_role'].get('router', active_m)}, Chat: {summary['last_used_by_role'].get('chat', active_m)})",
    ]

    cooling = summary["cooling_models"]
    if cooling:
        lines.append("• Models Currently Out of Quota / Cooling Down:")
        for m in cooling:
            lines.append(f"  - {m['key']} ({m['name']}): 0 requests remaining [Cooldown: {m['cooldown_remaining']} left, Reason: {m['reason']}]")
    else:
        lines.append("• Models Out of Quota: None (All engines operational)")

    healthy = summary["healthy_models"]
    lines.append("• Active Models & Remaining Requests Today:")
    for m in healthy:
        lines.append(f"  - {m['key']}: {m['remaining_requests']} / {m['daily_limit']} requests left today ({m['rpm']} RPM)")

    vision_models = [m["key"] for m in healthy if m.get("vision")]
    lines.append(f"• Multimodal Vision Models Available Right Now: {', '.join(vision_models) if vision_models else 'None (cooling down)'}")

    return "\n".join(lines)

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
        print(format_model_operational_context())
