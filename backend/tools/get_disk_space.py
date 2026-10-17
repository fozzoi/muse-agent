import sys, json, os, shutil

raw_input = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
target = "/"

if raw_input:
    try:
        data = json.loads(raw_input)
        if isinstance(data, dict):
            target = data.get("path") or data.get("mount") or data.get("directory") or next((str(v) for v in data.values() if isinstance(v, str)), "/")
        elif isinstance(data, str):
            target = data
    except Exception:
        target = str(raw_input)

usage = shutil.disk_usage(str(target))
total_gb = usage.total / (1024 ** 3)
used_gb = usage.used / (1024 ** 3)
free_gb = usage.free / (1024 ** 3)

result = {
    "total_gb": round(total_gb, 2),
    "used_gb": round(used_gb, 2),
    "free_gb": round(free_gb, 2)
}

print(json.dumps(result))