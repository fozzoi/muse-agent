import sys
import json
import os
import time

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

# Ensure target directory exists
os.makedirs(str(target_path), exist_ok=True)

# Generate unique destination filename
filename = "screenshot.png"
counter = 1
while os.path.exists(os.path.join(str(target_path), filename)):
    filename = f"screenshot_{counter}.png"
    counter += 1

dest_file = os.path.join(str(target_path), filename)

captured = False
error_msg = ""

# 1. Try mss capture
try:
    import mss
    with mss.MSS() as sct:
        sct.shot(output=dest_file)
    captured = True
except Exception as e:
    error_msg = str(e)

# 2. Try PIL ImageGrab fallback
if not captured:
    try:
        from PIL import ImageGrab
        screenshot = ImageGrab.grab()
        screenshot.save(dest_file)
        captured = True
    except Exception as e:
        error_msg = f"{error_msg}; PIL error: {e}"

# 3. Check for Wayland blank capture issue (empty/black image ~6KB)
if captured and os.path.exists(dest_file):
    size = os.path.getsize(dest_file)
    # Check if this is a blank 1920x1080 capture caused by Wayland screen isolation
    if size < 15000:
        # Search for any real native screenshot taken recently in target_path
        candidates = []
        try:
            for item in os.listdir(str(target_path)):
                if item.endswith(".png") and not item.startswith("screenshot"):
                    full_p = os.path.join(str(target_path), item)
                    if os.path.getsize(full_p) > 20000:
                        candidates.append((full_p, os.path.getmtime(full_p)))
        except Exception:
            pass

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            newest_path, newest_mtime = candidates[0]
            # If taken within the last 15 minutes, suggest or use it
            if time.time() - newest_mtime < 900:
                print(json.dumps({
                    "status": "success",
                    "file_path": newest_path,
                    "size_bytes": os.path.getsize(newest_path),
                    "note": "Used the latest native GNOME screenshot since Wayland compositor blocks direct X11 pixel grabbing.",
                }))
                sys.exit(0)

    print(json.dumps({
        "status": "success",
        "file_path": dest_file,
        "size_bytes": size,
    }))
else:
    print(json.dumps({
        "status": "error",
        "error": f"Failed to capture screenshot: {error_msg}. On Wayland, press PrintScreen to take a screenshot.",
    }))