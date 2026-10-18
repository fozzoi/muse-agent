import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)

def smart_resolve_path(raw_path: str, default_to_docs: bool = True) -> str:
    """
    Intelligently resolves user paths:
    - Normalizes home '~' expansion cleanly without path corruption
    - Performs fuzzy matching for case-insensitivity ('documents' -> 'Documents')
    - Handles space vs hyphen differences ('muse agent' -> 'muse-agent')
    - Searches against Muse project root, ~/Documents, and user home
    """
    if not raw_path or not str(raw_path).strip():
        return os.path.expanduser("~/Documents") if default_to_docs else ""

    clean = str(raw_path).strip().strip("'\"")
    expanded = os.path.expanduser(clean)
    if os.path.exists(expanded):
        return os.path.realpath(expanded)

    home = os.path.expanduser("~")
    rel_part = clean
    if rel_part.startswith("~/") or rel_part.startswith("~\\"):
        rel_part = rel_part[2:]
    elif rel_part.startswith("~"):
        rel_part = rel_part[1:].lstrip("/\\")

    anchors = [
        PROJECT_ROOT,
        os.path.join(PROJECT_ROOT, "backend"),
        home,
        os.path.join(home, "Documents"),
        os.getcwd(),
    ]

    def walk_and_resolve(base_dir: str, rel_p: str) -> str | None:
        parts = [p for p in rel_p.replace("\\", "/").split("/") if p and p != "."]
        curr = base_dir
        for part in parts:
            if not os.path.isdir(curr):
                return None
            target_candidates = {
                part.lower(),
                part.lower().replace(" ", "-"),
                part.lower().replace("-", " "),
                part.lower().replace(" ", "_"),
                part.lower().replace("_", " "),
            }
            found = None
            try:
                for entry in os.listdir(curr):
                    if entry.lower() in target_candidates:
                        found = os.path.join(curr, entry)
                        break
            except Exception:
                return None
            if not found:
                return None
            curr = found
        return curr

    for anchor in anchors:
        resolved = walk_and_resolve(anchor, rel_part)
        if resolved and os.path.exists(resolved):
            return os.path.realpath(resolved)

    if clean.startswith("/"):
        resolved = walk_and_resolve("/", clean.lstrip("/"))
        if resolved and os.path.exists(resolved):
            return os.path.realpath(resolved)

    return expanded
