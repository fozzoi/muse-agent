#!/usr/bin/env python3
"""Desktop Navigator Tool for Muse Agent.
Controls the user's real visible desktop screen, mouse, and keyboard.
Supports OCR-guided text finding and clicking ('find_and_click'), coordinate clicking ('click'),
typing ('type'), key presses ('key'), and mouse movement ('mousemove').
"""
import os
import sys
import json
import time
import subprocess

KEY_MAP = {
    "enter": ["28:1", "28:0"],
    "return": ["28:1", "28:0"],
    "space": ["57:1", "57:0"],
    "tab": ["15:1", "15:0"],
    "backspace": ["14:1", "14:0"],
    "esc": ["1:1", "1:0"],
    "escape": ["1:1", "1:0"],
    "super": ["125:1", "125:0"],
    "ctrl+l": ["29:1", "38:1", "38:0", "29:0"],
    "ctrl+t": ["29:1", "20:1", "20:0", "29:0"],
    "ctrl+w": ["29:1", "17:1", "17:0", "29:0"],
    "alt+f9": ["56:1", "67:1", "67:0", "56:0"],
    "alt+f4": ["56:1", "62:1", "62:0", "56:0"],
}

def get_env():
    env = os.environ.copy()
    uid = os.getuid()
    candidates = [
        os.environ.get("YDOTOOL_SOCKET", ""),
        f"/run/user/{uid}/.ydotool_socket",
        "/tmp/.ydotool_socket",
        f"/run/user/{uid}/ydotool_socket",
    ]
    sock = None
    for c in candidates:
        if c and os.path.exists(c):
            sock = c
            break
    if not sock:
        sock = f"/run/user/{uid}/.ydotool_socket"
    env["YDOTOOL_SOCKET"] = sock
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env

def capture_screen() -> str:
    """Captures a screenshot of the active desktop screen, reusing recent shots to avoid extra prompts."""
    screen_dir = os.path.expanduser("~/Pictures/Screenshots")
    os.makedirs(screen_dir, exist_ok=True)

    # First check: Was a screenshot taken in the last 15 seconds? Reuse it!
    candidates = []
    for cand_dir in [screen_dir, "/tmp"]:
        if os.path.isdir(cand_dir):
            for f in os.listdir(cand_dir):
                if f.endswith(".png"):
                    fp = os.path.join(cand_dir, f)
                    try:
                        mtime = os.path.getmtime(fp)
                        sz = os.path.getsize(fp)
                        if sz > 15000 and (time.time() - mtime < 15):
                            candidates.append((fp, mtime))
                    except Exception:
                        pass
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    shot_path = os.path.join(screen_dir, f"desktop_nav_{int(time.time())}.png")
    try:
        import pyscreenshot as ImageGrab
        img = ImageGrab.grab()
        img.save(shot_path)
        if os.path.exists(shot_path) and os.path.getsize(shot_path) > 15000:
            return shot_path
    except Exception:
        pass

    return shot_path

def _exec_input(ydotool_cmds: list[list[str]], xdotool_cmd: list[str]) -> bool:
    env = get_env()

    # Try ydotool commands
    ydotool_ok = True
    if ydotool_cmds:
        try:
            for cmd in ydotool_cmds:
                res = subprocess.run(cmd, env=env, capture_output=True, timeout=5)
                if res.returncode != 0:
                    ydotool_ok = False
                    break
        except Exception:
            ydotool_ok = False
    else:
        ydotool_ok = False

    if ydotool_ok:
        return True

    # Fallback to xdotool
    if xdotool_cmd:
        try:
            res = subprocess.run(xdotool_cmd, env=env, capture_output=True, timeout=5)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    return False

def run_action(action: str, x: int = 0, y: int = 0, text: str = "") -> dict:
    """Executes a low-level desktop input action."""
    action = (action or "click").lower().strip()
    x = int(x) if x is not None else 0
    y = int(y) if y is not None else 0
    text = str(text or "").strip()

    if action in ("click", "left_click"):
        ydotool_cmds = [
            ["ydotool", "mousemove", "-a", "-x", str(x), "-y", str(y)],
            ["ydotool", "click", "0xC0"],
        ]
        xdotool_cmd = ["xdotool", "mousemove", str(x), str(y), "click", "1"]
        ok = _exec_input(ydotool_cmds, xdotool_cmd)
        return {
            "status": "success" if ok else "error",
            "action": "click",
            "coordinates": {"x": x, "y": y},
            "message": f"Clicked at ({x}, {y})" if ok else "Failed to click",
        }

    elif action in ("double_click", "dblclick"):
        ydotool_cmds = [
            ["ydotool", "mousemove", "-a", "-x", str(x), "-y", str(y)],
            ["ydotool", "click", "0xC0"],
            ["ydotool", "click", "0xC0"],
        ]
        xdotool_cmd = ["xdotool", "mousemove", str(x), str(y), "click", "--repeat", "2", "--delay", "100", "1"]
        ok = _exec_input(ydotool_cmds, xdotool_cmd)
        return {
            "status": "success" if ok else "error",
            "action": "double_click",
            "coordinates": {"x": x, "y": y},
            "message": f"Double clicked at ({x}, {y})" if ok else "Failed to double click",
        }

    elif action in ("mousemove", "move"):
        ydotool_cmds = [["ydotool", "mousemove", "-a", "-x", str(x), "-y", str(y)]]
        xdotool_cmd = ["xdotool", "mousemove", str(x), str(y)]
        ok = _exec_input(ydotool_cmds, xdotool_cmd)
        return {
            "status": "success" if ok else "error",
            "action": "mousemove",
            "coordinates": {"x": x, "y": y},
            "message": f"Moved cursor to ({x}, {y})" if ok else "Failed to move cursor",
        }

    elif action in ("type", "write"):
        if not text:
            return {"status": "error", "message": "No text provided to type"}
        ydotool_cmds = [["ydotool", "type", "--", text]]
        xdotool_cmd = ["xdotool", "type", "--delay", "40", "--", text]
        ok = _exec_input(ydotool_cmds, xdotool_cmd)
        return {
            "status": "success" if ok else "error",
            "action": "type",
            "typed_text": text,
            "message": f"Typed '{text}'" if ok else "Failed to type text",
        }

    elif action in ("key", "keypress", "press"):
        if not text:
            return {"status": "error", "message": "No key provided to press"}
        k_lower = text.lower()
        if k_lower in KEY_MAP:
            ydotool_cmds = [["ydotool", "key"] + KEY_MAP[k_lower]]
        else:
            ydotool_cmds = [["ydotool", "key", text]]
        xdotool_cmd = ["xdotool", "key", text]
        ok = _exec_input(ydotool_cmds, xdotool_cmd)
        return {
            "status": "success" if ok else "error",
            "action": "key",
            "key": text,
            "message": f"Pressed key '{text}'" if ok else "Failed to press key",
        }

    elif action in ("focus", "focus_window", "open_app", "launch"):
        target = text.lower() if text else "chrome"
        if "chrome" in target:
            try:
                subprocess.Popen(["google-chrome", "https://www.youtube.com" if "youtube" in target else ""])
                time.sleep(1)
                return {"status": "success", "action": "focus", "message": "Brought Google Chrome to foreground"}
            except Exception as e:
                return {"status": "error", "message": f"Failed to launch Chrome: {e}"}
        elif "zen" in target:
            try:
                subprocess.Popen(["zen-browser"])
                time.sleep(1)
                return {"status": "success", "action": "focus", "message": "Brought Zen Browser to foreground"}
            except Exception as e:
                return {"status": "error", "message": f"Failed to launch Zen: {e}"}
        else:
            try:
                subprocess.run(["wmctrl", "-a", text], capture_output=True, timeout=2)
                return {"status": "success", "action": "focus", "message": f"Focused window '{text}'"}
            except Exception as e:
                return {"status": "error", "message": f"Failed to focus window: {e}"}

    elif action in ("minimize", "window_minimize", "hide"):
        # Use GNOME Wayland shortcut (Alt+F9 or Super+h) or wmctrl (NEVER use pkill!)
        if text:
            try:
                subprocess.run(["wmctrl", "-r", text, "-b", "add,hidden"], env=get_env(), capture_output=True, timeout=3)
            except Exception:
                pass
        xdotool_cmd = ["xdotool", "key", "--clearmodifiers", "Alt+F9"]
        ok = _exec_input([], xdotool_cmd)
        return {
            "status": "success" if ok else "error",
            "action": "minimize",
            "message": "Minimized active window using Alt+F9 shortcut" if ok else "Failed to minimize",
        }

    elif action in ("close_window", "close_app"):
        xdotool_cmd = ["xdotool", "key", "--clearmodifiers", "Alt+F4"]
        ok = _exec_input([], xdotool_cmd)
        return {
            "status": "success" if ok else "error",
            "action": "close_window",
            "message": "Closed active window using Alt+F4 shortcut" if ok else "Failed to close window",
        }

    return {"status": "error", "message": f"Unknown action '{action}'"}

def find_and_click_text(target_text: str) -> dict:
    """Takes a screenshot, locates target text bounding box via pytesseract, and clicks the center."""
    import time
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return {
            "status": "error",
            "message": "pytesseract or Pillow is not installed. Install via pip install pytesseract pillow"
        }

    shot_path = capture_screen()
    img = Image.open(shot_path)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

    target_lower = target_text.lower().strip()
    n_boxes = len(data["text"])

    # First check: multi-word sequence match across consecutive OCR boxes
    target_words = target_lower.split()
    if len(target_words) > 1:
        phrase_len = len(target_words)
        for i in range(n_boxes - phrase_len + 1):
            window_words = [data["text"][i + j].strip().lower() for j in range(phrase_len)]
            window_str = " ".join(window_words)
            if target_lower == window_str or target_lower in window_str:
                start_x = data["left"][i]
                end_x = data["left"][i + phrase_len - 1] + data["width"][i + phrase_len - 1]
                x = (start_x + end_x) // 2
                y = data["top"][i] + (data["height"][i] // 2)
                click_res = run_action("click", x=x, y=y)
                click_res["matched_text"] = " ".join([data["text"][i + j] for j in range(phrase_len)])
                click_res["found_coordinates"] = {"x": x, "y": y}
                click_res["screenshot"] = shot_path
                return click_res

    # Second check: exact phrase match in individual text chunks
    for i in range(n_boxes):
        word = data["text"][i].strip().lower()
        if not word:
            continue
        # Exact match or target phrase is inside the word (e.g. "youtube" in "youtube.com")
        # NEVER match if word is just an arbitrary small substring of target_text (avoids false positive clicks on "out", "you", etc.)
        if word == target_lower or target_lower in word:
            x = data["left"][i] + (data["width"][i] // 2)
            y = data["top"][i] + (data["height"][i] // 2)
            click_res = run_action("click", x=x, y=y)
            click_res["matched_text"] = data["text"][i]
            click_res["found_coordinates"] = {"x": x, "y": y}
            click_res["screenshot"] = shot_path
            return click_res

    return {
        "status": "error",
        "message": f"Could not visually locate '{target_text}' on the active screen. Screenshot saved to {shot_path}",
        "screenshot": shot_path
    }

def main():
    raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
    try:
        args = json.loads(raw)
        if not isinstance(args, dict):
            args = {"target_text": str(args)}
    except Exception:
        args = {"target_text": str(raw)}

    action = str(args.get("action", "")).lower().strip()
    target_text = args.get("target_text") or args.get("text") or args.get("query") or args.get("title")

    # If action is find_and_click or target_text is explicitly provided
    if action == "find_and_click" or (target_text and action not in ("type", "key")):
        res = find_and_click_text(target_text)
    elif action in ("click", "double_click", "mousemove", "type", "key"):
        x = args.get("x")
        y = args.get("y")
        text_arg = args.get("text") or args.get("key") or ""
        res = run_action(action, x=x, y=y, text=text_arg)
    else:
        # Default fallback: if target_text exists, find_and_click
        if target_text:
            res = find_and_click_text(target_text)
        else:
            res = {
                "status": "error",
                "message": "Missing action or target_text. Specify action ('find_and_click', 'click', 'type', 'key') and required parameters."
            }

    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
