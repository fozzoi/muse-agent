import sys
import json
import os

raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"path": str(args)}
except Exception:
    args = {"path": raw}

target = (
    args.get("path")
    or args.get("file_path")
    or args.get("file")
    or args.get("target")
    or ""
)

if not target:
    print("Error: No file path provided.", file=sys.stderr)
    sys.exit(1)

def resolve_path(p: str) -> str:
    if not p:
        return os.path.expanduser("~")
    p = os.path.expanduser(str(p).strip().strip("'\""))
    if os.path.isabs(p) and os.path.exists(p):
        return os.path.realpath(p)
    for base in [os.path.expanduser("~"), os.path.expanduser("~/Documents"), os.path.expanduser("~/Documents/muse-agent")]:
        cand = os.path.join(base, p.lstrip("/\\"))
        if os.path.exists(cand):
            return os.path.realpath(cand)
    return os.path.join(os.path.expanduser("~"), p.lstrip("/\\"))

target_path = resolve_path(target)

if not os.path.exists(target_path):
    print(f"File not found: {target_path}", file=sys.stderr)
    sys.exit(1)

if os.path.isdir(target_path):
    print(f"Path is a directory: {target_path}. Use fs_list instead.", file=sys.stderr)
    sys.exit(1)

try:
    size = os.path.getsize(target_path)
    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(10000)

    truncated = "\n\n[Content truncated after 10,000 characters...]" if size > 10000 else ""
    print(content + truncated)
except Exception as e:
    print(f"Error reading file '{target_path}': {e}", file=sys.stderr)
    sys.exit(1)
