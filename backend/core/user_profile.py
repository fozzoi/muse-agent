import os
import json
import time
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
PROFILE_FILE = os.path.join(DATA_DIR, "user_profile.json")

_lock = threading.Lock()

DEFAULT_PROFILE = {
    "user_id": "850987998",
    "location": "Nilambur, Kerala",
    "timezone": "Asia/Kolkata",
    "hardware": {
        "os": "Ubuntu Linux",
        "desktop_environment": "GNOME (Wayland/Xwayland)",
        "laptop_model": "HP Victus 15"
    },
    "applications": {
        "primary_browser": "Zen Browser",
        "secondary_browser": "Google Chrome",
        "audio_system": "PipeWire / WirePlumber"
    },
    "preferences": {
        "media_control": "Visible YouTube on desktop screen via desktop_navigator (never headless browser_agent)",
        "window_management": "Use Alt+F9 / Super+h to minimize, Alt+F4 to close (NEVER call pkill to minimize)",
        "browser_usage": "desktop_navigator for real visible screen control; browser_agent strictly for silent background scraping"
    },
    "learned_rules": [
        "User location is Nilambur, Kerala - automatically use for weather, time, and regional queries.",
        "Never use 'pkill' or 'kill -9' to minimize applications; use GNOME shortcuts (Alt+F9 / Super+h).",
        "Always use desktop_navigator (OCR-guided clicking) for YouTube, music, or active screen interactions.",
        "Headless browser_agent is strictly for silent background web scraping, never for user media playback.",
        "Primary browser is Zen Browser; Google Chrome is used for active media and YouTube tasks.",
        "MANDATORY VERIFICATION: Never claim a desktop/system task is complete without taking a follow-up screenshot or inspecting command output; never bluff or assume success.",
    ],
    "memories": [
        {"timestamp": 1792578500, "fact": "User lives in Nilambur, Kerala."},
        {"timestamp": 1792580000, "fact": "User primary browser is Zen Browser; secondary is Google Chrome."},
        {"timestamp": 1792581000, "fact": "User requires desktop mouse and vision control for YouTube playback, not background headless tabs."},
        {"timestamp": 1792581500, "fact": "User prefers 'One Sun One Moon' from Jailer 2 for music tests."}
    ]
}

def load_profile() -> dict:
    """Loads the user's evolved profile from data/user_profile.json."""
    if not os.path.exists(PROFILE_FILE):
        os.makedirs(DATA_DIR, exist_ok=True)
        save_profile(DEFAULT_PROFILE)
        return DEFAULT_PROFILE.copy()
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return DEFAULT_PROFILE.copy()
    except Exception as e:
        print(f"[UserProfile] Error loading profile: {e}", flush=True)
        return DEFAULT_PROFILE.copy()

def save_profile(profile: dict):
    """Persists the user profile atomically."""
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = f"{PROFILE_FILE}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2)
            os.replace(tmp, PROFILE_FILE)
        except Exception as e:
            print(f"[UserProfile] Error saving profile: {e}", flush=True)

def get_user_profile_context() -> str:
    """
    Formats the evolved user profile into a compact, high-impact block
    to inject directly into the router and agent planning prompts.
    """
    from datetime import datetime
    now_str = datetime.now().strftime("%A, %B %d, %Y - %I:%M %p %Z")

    profile = load_profile()
    lines = [
        f"CURRENT REAL-TIME SYSTEM CLOCK: {now_str}",
        "EVOLVED USER PROFILE & PREFERENCES (Learned from past interactions):",
        f"• User Location: {profile.get('location', 'Nilambur, Kerala')}",
        f"• Desktop Environment: {profile.get('hardware', {}).get('os', 'Ubuntu Linux')} - {profile.get('hardware', {}).get('desktop_environment', 'GNOME Wayland')}",
        f"• Installed Browsers: {profile.get('applications', {}).get('primary_browser', 'Zen Browser')} (primary), {profile.get('applications', {}).get('secondary_browser', 'Google Chrome')}",
    ]

    prefs = profile.get("preferences", {})
    if prefs:
        lines.append("• Key Preferences:")
        for k, v in prefs.items():
            lines.append(f"  - {k}: {v}")

    rules = profile.get("learned_rules", [])
    if rules:
        lines.append("• Critical Behavioral Rules (Avoid repeating past mistakes):")
        for r in rules[-8:]:
            lines.append(f"  - {r}")

    try:
        from core.quota_manager import format_model_operational_context
        lines.append("\n" + format_model_operational_context())
    except Exception as e:
        print(f"[UserProfile] Could not format model quota context: {e}", flush=True)

    return "\n".join(lines)

def add_learned_rule(rule: str):
    """Adds a learned rule if not already present."""
    if not rule or len(rule.strip()) < 5:
        return
    rule = rule.strip()
    profile = load_profile()
    rules = profile.get("learned_rules", [])
    if not any(rule.lower() in existing.lower() or existing.lower() in rule.lower() for existing in rules):
        rules.append(rule)
        profile["learned_rules"] = rules
        save_profile(profile)
        print(f"[UserProfile] 🧠 Learned new user rule: '{rule}'", flush=True)

def add_memory(fact: str):
    """Records an episodic memory fact."""
    if not fact or len(fact.strip()) < 5:
        return
    fact = fact.strip()
    profile = load_profile()
    memories = profile.get("memories", [])
    if not any(fact.lower() in m.get("fact", "").lower() for m in memories):
        memories.append({"timestamp": int(time.time()), "fact": fact})
        # Keep recent 30 memories
        if len(memories) > 30:
            memories = memories[-30:]
        profile["memories"] = memories
        save_profile(profile)
        print(f"[UserProfile] 📝 Recorded new memory fact: '{fact}'", flush=True)

def update_profile_field(field: str, value: any):
    """Updates a top-level field in user profile (e.g. location)."""
    profile = load_profile()
    profile[field] = value
    save_profile(profile)
    print(f"[UserProfile] 🔄 Updated profile field '{field}' = {value}", flush=True)

def format_profile_for_telegram() -> str:
    """Returns a clean mobile-friendly Telegram summary of what Muse knows."""
    profile = load_profile()
    lines = [
        "🧠 *Muse Evolved User Profile & Memory*",
        "",
        f"📍 *Location:* `{profile.get('location', 'Nilambur, Kerala')}`",
        f"💻 *Environment:* `{profile.get('hardware', {}).get('desktop_environment', 'Ubuntu GNOME')}`",
        f"🌐 *Browsers:* `{profile.get('applications', {}).get('primary_browser', 'Zen Browser')}`, `{profile.get('applications', {}).get('secondary_browser', 'Chrome')}`",
        "",
        "⚡ *Learned Behavioral Rules:*",
    ]
    for r in profile.get("learned_rules", []):
        lines.append(f"• {r}")

    memories = profile.get("memories", [])
    if memories:
        lines.append("")
        lines.append("📝 *Recent Memory Facts:*")
        for m in memories[-5:]:
            lines.append(f"• {m.get('fact')}")

    return "\n".join(lines)

if __name__ == "__main__":
    print(get_user_profile_context())
