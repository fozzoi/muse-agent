import sys
import json
import os

raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
target = ""

if raw:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            target = (
                data.get("path")
                or data.get("directory")
                or data.get("folder")
                or data.get("target")
                or ""
            )
        elif isinstance(data, str):
            target = data
    except Exception:
        target = str(raw).strip()

# Path normalization & resolution:
if not target:
    target_path = os.path.expanduser("~/Pictures/Screenshots")
else:
    expanded = os.path.expanduser(target)
    if os.path.isabs(expanded) and os.path.exists(expanded):
        target_path = expanded
    else:
        home_candidate = os.path.join(os.path.expanduser("~"), target)
        pic_candidate = os.path.join(os.path.expanduser("~/Pictures"), target)
        if os.path.exists(home_candidate):
            target_path = home_candidate
        elif os.path.exists(pic_candidate):
            target_path = pic_candidate
        else:
            target_path = pic_candidate

# Ensure the target directory exists
os.makedirs(str(target_path), exist_ok=True)

# Generate a unique filename
filename = "screenshot.png"
counter = 1
while os.path.exists(os.path.join(str(target_path), filename)):
    filename = f"screenshot_{counter}.png"
    counter += 1

dest_file = os.path.join(str(target_path), filename)

# Capture screenshot with mss or PIL fallback
captured = False
error_msg = ""

try:
    import mss
    with mss.MSS() as sct:
        sct.shot(output=dest_file)
    captured = True
except Exception as e:
    error_msg = str(e)

if not captured:
    try:
        from PIL import ImageGrab
        screenshot = ImageGrab.grab()
        screenshot.save(dest_file)
        captured = True
    except Exception as e:
        error_msg = f"{error_msg}; PIL error: {e}"

if captured and os.path.exists(dest_file):
    print(json.dumps({
        "status": "success",
        "file_path": dest_file,
        "size_bytes": os.path.getsize(dest_file),
    }))
else:
    print(json.dumps({
        "status": "error",
        "error": f"Failed to capture screenshot: {error_msg}"
    }))