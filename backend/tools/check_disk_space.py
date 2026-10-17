import sys, json, os, shutil

raw_input = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw_input)
    if isinstance(args, str):
        args = json.loads(args) if args.startswith("{") else {"input": args}
except Exception:
    args = {"input": raw_input}

path = args.get("path") or args.get("mount") or args.get("input") or "/"
if isinstance(path, dict):
    path = path.get("input") or "/"

total, used, free = shutil.disk_usage(path)
total_gb = total // (2**30)
used_gb = used // (2**30)
free_gb = free // (2**30)

print(json.dumps({"total": total_gb, "used": used_gb, "free": free_gb}))