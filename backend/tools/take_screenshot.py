import sys, json, os
from PIL import ImageGrab

def main():
    raw_input = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
    filename = "screenshot.png"

    if raw_input:
        try:
            data = json.loads(raw_input)
            if isinstance(data, dict):
                filename = data.get("filename") or filename
            elif isinstance(data, str):
                filename = data
        except Exception:
            filename = str(raw_input)

    screenshot = ImageGrab.grab()
    screenshot_path = os.path.abspath(str(filename))
    screenshot.save(screenshot_path)

    print(json.dumps({"screenshot_path": screenshot_path}))

if __name__ == "__main__":
    main()