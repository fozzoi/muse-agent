import sys
import json
import os
import time
import subprocess
import shutil

raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"url": str(args)}
except Exception:
    args = {"url": raw}

url = args.get("url") or args.get("website") or args.get("link") or ""
if not url:
    print("Error: 'url' parameter is required (e.g. {'url': 'https://github.com'}).", file=sys.stderr)
    sys.exit(1)

# Ensure schema
if not url.startswith("http://") and not url.startswith("https://"):
    url = f"https://{url}"

chrome_bin = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium") or shutil.which("chromium-browser")
if not chrome_bin:
    print("Error: No Chromium/Chrome browser binary found on system.", file=sys.stderr)
    sys.exit(1)

timestamp = time.strftime("%Y%m%d_%H%M%S")
dest_file = f"/tmp/web_{timestamp}.png"

try:
    cmd = [
        chrome_bin,
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size=1280,800",
        f"--screenshot={dest_file}",
        url
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    
    if os.path.exists(dest_file) and os.path.getsize(dest_file) > 1000:
        print(json.dumps({
            "status": "success",
            "url": url,
            "file_path": dest_file,
            "size_bytes": os.path.getsize(dest_file)
        }, indent=2))
    else:
        err = res.stderr.strip() or "Failed to capture screenshot."
        print(f"Error capturing webpage: {err}", file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print(f"Exception during web screenshot: {e}", file=sys.stderr)
    sys.exit(1)
