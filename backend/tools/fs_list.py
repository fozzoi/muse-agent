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
    or args.get("directory")
    or args.get("folder")
    or args.get("target")
    or ""
)

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
    print(f"Directory not found: {target_path}", file=sys.stderr)
    sys.exit(1)

if not os.path.isdir(target_path):
    size = os.path.getsize(target_path)
    print(json.dumps({"path": target_path, "type": "file", "size_bytes": size}))
    sys.exit(0)

try:
    entries = []
    for item in sorted(os.listdir(target_path)):
        full = os.path.join(target_path, item)
        is_dir = os.path.isdir(full)
        sz = None
        if not is_dir:
            try:
                sz = os.path.getsize(full)
            except Exception:
                sz = None
        entries.append({
            "name": item,
            "type": "directory" if is_dir else "file",
            "size_bytes": sz,
        })
    print(json.dumps({
        "path": target_path,
        "total": len(entries),
        "entries": entries,
    }, indent=2))
except Exception as e:
    print(f"Error accessing directory: {e}", file=sys.stderr)
    sys.exit(1)
