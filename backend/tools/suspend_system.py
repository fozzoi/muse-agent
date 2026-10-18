import sys, json, os, subprocess

raw_input = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
target = "/"  # sensible fallback default

if raw_input:
    try:
        data = json.loads(raw_input)
        if isinstance(data, dict):
            # Extract first non-empty string value or specific keys
            target = data.get("path") or data.get("mount") or data.get("directory") or next((str(v) for v in data.values() if isinstance(v, str)), "/")
        elif isinstance(data, str):
            target = data
    except Exception:
        target = str(raw_input)

try:
    subprocess.run(["systemctl", "suspend"], check=True)
    print(json.dumps({"status": "suspended"}))
except subprocess.CalledProcessError as e:
    print(json.dumps({"status": "error", "message": str(e)}))
except Exception as e:
    print(json.dumps({"status": "error", "message": str(e)}))