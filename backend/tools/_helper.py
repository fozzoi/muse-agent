import os
import sys
import json

def parse_args() -> dict:
    raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
    try:
        args = json.loads(raw)
        if not isinstance(args, dict):
            args = {"input": str(args)}
    except Exception:
        args = {"input": raw}
    return args

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
