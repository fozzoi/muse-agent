import sys, json, os, platform, multiprocessing, shutil

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

def get_disk_usage(path):
    usage = shutil.disk_usage(path)
    return {
        "total": usage.total / (1024 ** 3),
        "used": usage.used / (1024 ** 3),
        "free": usage.free / (1024 ** 3)
    }

system_info = {
    "os": platform.system(),
    "python_version": platform.python_version(),
    "cpu_count": multiprocessing.cpu_count(),
    "disk_usage": get_disk_usage(target)
}

print(json.dumps(system_info))