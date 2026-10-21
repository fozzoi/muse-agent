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

def get_env():
    env = os.environ.copy()
    env.setdefault("YDOTOOL_SOCKET", "/tmp/.ydotool_socket")
    env.setdefault("DISPLAY", ":0")
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env

def capture_screen() -> str:
    """Captures a screenshot of the active desktop screen, saving it to ~/Pictures/Screenshots or /tmp."""
    screen_dir = os.path.expanduser("~/Pictures/Screenshots")
    os.makedirs(screen_dir, exist_ok=True)
    shot_path = os.path.join(screen_dir, f"desktop_nav_{int(time.time())}.png")

    # 1. Primary: pyscreenshot (Wayland portals & X11)
    try:
        import pyscreenshot as ImageGrab
        img = ImageGrab.grab()
        img.save(shot_path)
        if os.path.exists(shot_path) and os.path.getsize(shot_path) > 15000:
            return shot_path
    except Exception:
        pass

    # 2. Fallback: mss
    try:
        import mss
        with mss.MSS() as sct:
            sct.shot(output=shot_path)
        if os.path.exists(shot_path) and os.path.getsize(shot_path) > 15000:
            return shot_path
    except Exception:
        pass

    # 3. Fallback: PIL ImageGrab
    try:
        from PIL import ImageGrab as PilGrab
        img = PilGrab.grab()
        img.save(shot_path)
        if os.path.exists(shot_path) and os.path.getsize(shot_path) > 15000:
            return shot_path
    except Exception:
        pass

    # 4. Search existing recent screenshots
    for cand_dir in [screen_dir, "/tmp"]:
        if os.path.isdir(cand_dir):
            for f in sorted(os.listdir(cand_dir), reverse=True):
                if f.endswith(".png"):
                    fp = os.path.join(cand_dir, f)
                    try:
                        if os.path.getsize(fp) > 15000:
                            return fp
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
            ["ydotool", "mousemove", "-w", str(x), str(y)],
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
            ["ydotool", "mousemove", "-w", str(x), str(y)],
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
        ydotool_cmds = [["ydotool", "mousemove", "-w", str(x), str(y)]]
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
        ydotool_cmds = [["ydotool", "key", text]]
        xdotool_cmd = ["xdotool", "key", text]
        ok = _exec_input(ydotool_cmds, xdotool_cmd)
        return {
            "status": "success" if ok else "error",
            "action": "key",
            "key": text,
            "message": f"Pressed key '{text}'" if ok else "Failed to press key",
        }

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
        # Exact match or substantial substring match (avoid false positives on 1-2 char words)
        if word == target_lower or target_lower in word or (len(word) >= 3 and word in target_lower):
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
