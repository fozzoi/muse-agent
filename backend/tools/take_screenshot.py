import sys
import json
import os
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from core.path_resolver import smart_resolve_path

def main():
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

    if not target:
        target_dir = smart_resolve_path("~/Pictures/Screenshots", default_to_docs=False)
    else:
        resolved = smart_resolve_path(target, default_to_docs=False)
        target_dir = resolved if os.path.isdir(resolved) else os.path.dirname(resolved)

    os.makedirs(target_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    dest_file = os.path.join(target_dir, f"screenshot_{timestamp}.png")

    captured = False
    error_msg = ""

    # 1. Primary: pyscreenshot (supports Wayland portals and X11 cleanly)
    try:
        import pyscreenshot as ImageGrab
        img = ImageGrab.grab()
        img.save(dest_file)
        if os.path.exists(dest_file) and os.path.getsize(dest_file) > 15000:
            captured = True
    except Exception as e:
        error_msg = f"pyscreenshot error: {e}"

    # 2. Fallback: mss
    if not captured:
        try:
            import mss
            with mss.MSS() as sct:
                sct.shot(output=dest_file)
            if os.path.exists(dest_file) and os.path.getsize(dest_file) > 15000:
                captured = True
        except Exception as e:
            error_msg += f"; mss error: {e}"

    # 3. Fallback: PIL ImageGrab
    if not captured:
        try:
            from PIL import ImageGrab as PilGrab
            img = PilGrab.grab()
            img.save(dest_file)
            if os.path.exists(dest_file) and os.path.getsize(dest_file) > 15000:
                captured = True
        except Exception as e:
            error_msg += f"; PIL error: {e}"

    # 4. Check for Wayland blank black canvas (size < 15KB)
    if os.path.exists(dest_file) and os.path.getsize(dest_file) < 15000:
        captured = False

    if not captured:
        # Search for any recent valid screenshot in ~/Pictures/Screenshots or /tmp
        search_dirs = [target_dir, os.path.expanduser("~/Pictures/Screenshots"), "/tmp"]
        candidates = []
        for sdir in search_dirs:
            if os.path.isdir(sdir):
                try:
                    for f in os.listdir(sdir):
                        if f.lower().endswith(".png") and not f.startswith("screenshot_20"):
                            fp = os.path.join(sdir, f)
                            sz = os.path.getsize(fp)
                            if sz > 20000:
                                candidates.append((fp, os.path.getmtime(fp), sz))
                except Exception:
                    pass
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_file, best_time, best_size = candidates[0]
            if time.time() - best_time < 1800:
                print(json.dumps({
                    "status": "success",
                    "file_path": best_file,
                    "size_bytes": best_size,
                    "note": "Used the most recent desktop screenshot.",
                }))
                return

    if os.path.exists(dest_file):
        print(json.dumps({
            "status": "success",
            "file_path": dest_file,
            "size_bytes": os.path.getsize(dest_file),
        }))
    else:
        print(json.dumps({
            "status": "error",
            "error": f"Failed to capture screenshot: {error_msg}",
        }))

if __name__ == "__main__":
    main()