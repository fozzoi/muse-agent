import os
import json
from typing import List, Dict

JOURNAL_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "journal.json")

DEFAULT_LESSONS = [
    {
        "id": "wayland-screenshots",
        "title": "Screen Capture on Ubuntu GNOME Wayland",
        "problem": "Standard X11 tools (mss, PIL.ImageGrab) produce a blank 6KB black image on Wayland/Mutter.",
        "solution": "Use headless Google Chrome ('google-chrome --headless --screenshot=...') for webpages, or pyscreenshot with portal for desktop captures.",
        "tags": ["screenshot", "wayland", "desktop", "display"]
    },
    {
        "id": "path-resolution",
        "title": "Path Normalization and Fuzzy Matching",
        "problem": "User queries often include '~', relative paths, or typos/spaces (e.g., 'muse agent' vs 'muse-agent').",
        "solution": "Use smart_resolve_path() from core.path_resolver to expand '~', check PROJECT_ROOT, and resolve space/hyphen variations.",
        "tags": ["filesystem", "paths", "files", "directories"]
    },
    {
        "id": "desktop-env-vars",
        "title": "Desktop Environment Variables for Subprocesses",
        "problem": "System tools for audio, media (playerctl, pactl, wpctl), and notifications fail without runtime bus addresses.",
        "solution": "Always ensure env has XDG_RUNTIME_DIR set to /run/user/<uid> and DBUS_SESSION_BUS_ADDRESS to unix:path=/run/user/<uid>/bus.",
        "tags": ["environment", "audio", "dbus", "system"]
    },
    {
        "id": "package-auto-install",
        "title": "Automated Dependency Recovery",
        "problem": "Dynamic scripts fail if a required third-party module is not installed in the virtual environment.",
        "solution": "Catch ModuleNotFoundError, map the module to its pip package name, run pip install inside backend/venv, and re-execute.",
        "tags": ["pip", "dependencies", "modules", "self-healing"]
    },
    {
        "id": "tool-chaining",
        "title": "Combine Existing Tools Before Making New Tools",
        "problem": "Compound requests (e.g. check system stats and list files) waste time synthesizing redundant new scripts.",
        "solution": "Chain primitives sequentially (e.g. sys_info -> fs_list -> FINISH) in the multi-step agent loop instead of MAKE_TOOL.",
        "tags": ["planning", "orchestration", "tools"]
    }
]

def load_journal() -> List[Dict]:
    """Loads the agent's learned lessons from persistent storage."""
    if not os.path.exists(JOURNAL_FILE):
        os.makedirs(os.path.dirname(JOURNAL_FILE), exist_ok=True)
        save_journal(DEFAULT_LESSONS)
        return DEFAULT_LESSONS
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return DEFAULT_LESSONS
    except Exception as e:
        print(f"[Journal Warning] Could not read journal: {e}")
        return DEFAULT_LESSONS

def save_journal(journal: List[Dict]):
    """Persists lessons to data/journal.json."""
    os.makedirs(os.path.dirname(JOURNAL_FILE), exist_ok=True)
    try:
        with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
            json.dump(journal, f, indent=2)
    except Exception as e:
        print(f"[Journal Warning] Could not save journal: {e}")

def record_lesson(title: str, problem: str, solution: str, tags: List[str] | None = None):
    """Adds a newly discovered solution to the agent's experience journal."""
    journal = load_journal()
    # Check if a lesson with similar title already exists
    for entry in journal:
        if entry.get("title", "").lower() == title.lower():
            entry["problem"] = problem
            entry["solution"] = solution
            if tags:
                entry["tags"] = list(set(entry.get("tags", []) + tags))
            save_journal(journal)
            return

    new_id = f"lesson-{len(journal) + 1}"
    journal.append({
        "id": new_id,
        "title": title,
        "problem": problem,
        "solution": solution,
        "tags": tags or []
    })
    save_journal(journal)
    print(f"[Journal] 📘 Recorded new lesson: '{title}'", flush=True)

def get_journal_context(query: str = "", limit: int = 4) -> str:
    """Returns a formatted summary of relevant learned lessons for prompt injection."""
    journal = load_journal()
    if not journal:
        return ""

    if not query:
        selected = journal[:limit]
    else:
        q_words = set(query.lower().split())
        scored = []
        for entry in journal:
            score = 0
            text_corpus = (
                entry.get("title", "") + " " +
                entry.get("problem", "") + " " +
                entry.get("solution", "") + " " +
                " ".join(entry.get("tags", []))
            ).lower()
            for w in q_words:
                if len(w) > 3 and w in text_corpus:
                    score += 1
            scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [item[1] for item in scored[:limit]]

    lines = []
    for item in selected:
        lines.append(f"• **{item.get('title')}**\n  - Issue: {item.get('problem')}\n  - Rule/Fix: {item.get('solution')}")

    return "\n".join(lines)
