import sys
import json
import os
import subprocess

def install_package(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

try:
    import pyscreenshot
except ImportError:
    install_package('pyscreenshot')

raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
target = ""

if raw:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            target = (
                data.get("output_path")
                or data.get("path")
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
    target_path = os.path.expanduser("~/Pictures/Screenshots/screenshot.png")
else:
    expanded = os.path.expanduser(target)
    if os.path.isabs(expanded):
        target_path = expanded
    else:
        # Check relative to ~ and ~/Pictures/Screenshots
        home_candidate = os.path.join(os.path.expanduser("~"), target)
        pic_candidate = os.path.join(os.path.expanduser("~/Pictures/Screenshots"), target)
        if os.path.exists(home_candidate):
            target_path = home_candidate
        elif os.path.exists(pic_candidate):
            target_path = pic_candidate
        else:
            target_path = pic_candidate

# Ensure the directory exists
os.makedirs(os.path.dirname(target_path), exist_ok=True)

# Capture and save screenshot
image = pyscreenshot.grab()
image.save(str(target_path))

print(f"Screenshot saved to {target_path}")