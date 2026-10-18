import os
import json
from pathlib import Path

MEMORY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sessions")
os.makedirs(MEMORY_DIR, exist_ok=True)

def _get_session_file(session_id: str) -> str:
    safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_")) or "default"
    return os.path.join(MEMORY_DIR, f"{safe_id}.json")

def load_session(session_id: str) -> list[dict]:
    filepath = _get_session_file(session_id)
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Memory Warning] Could not read session {session_id}: {e}")
            return []
    return []

def save_session(session_id: str, history: list[dict]):
    filepath = _get_session_file(session_id)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[Memory Warning] Could not save session {session_id}: {e}")

def add_message(session_id: str, role: str, content: str, extra: dict | None = None):
    history = load_session(session_id)
    msg = {"role": role, "content": content}
    if extra:
        msg.update(extra)
    history.append(msg)
    # Retain up to 40 recent messages per session for context depth
    if len(history) > 40:
        history = history[-40:]
    save_session(session_id, history)

def clear_session(session_id: str):
    filepath = _get_session_file(session_id)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            pass

def get_history(session_id: str, limit: int = 10) -> list[dict]:
    history = load_session(session_id)
    return history[-limit:]

def format_history_context(session_id: str, limit: int = 8) -> str:
    history = load_session(session_id)
    recent = history[-limit:]
    if not recent:
        return "None"
    lines = []
    for msg in recent:
        role = msg.get("role", "user").upper()
        content = msg.get("content", "").strip()
        lines.append(f"{role}: {content}")
    return "\n".join(lines)
