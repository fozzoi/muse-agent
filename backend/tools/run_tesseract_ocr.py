import sys, json, os
import subprocess
import shutil

# Input parsing (Rule 1)
raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}

# Path resolution (Rule 2)
def resolve_path(p: str) -> str:
    if not p:
        return os.path.expanduser("~")
    p = os.path.expanduser(str(p).strip())
    if os.path.isabs(p) and os.path.exists(p):
        return p
    for base in [os.path.expanduser("~"), os.path.expanduser("~/Documents"), os.path.expanduser("~/Documents/muse-agent")]:
        cand = os.path.join(base, p.lstrip("/\\"))
        if os.path.exists(cand):
            return cand
    return os.path.join(os.path.expanduser("~"), p.lstrip("/\\"))

def main():
    image_path_str = args.get("path")
    if not image_path_str:
        print("Error: 'path' argument is required.", file=sys.stderr)
        sys.exit(1)

    resolved_image_path = resolve_path(image_path_str)

    if not os.path.exists(resolved_image_path):
        print(f"Error: Image file not found at '{resolved_image_path}'", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(resolved_image_path):
        print(f"Error: Path '{resolved_image_path}' is not a file.", file=sys.stderr)
        sys.exit(1)

    # Check if tesseract is installed
    tesseract_cmd = shutil.which("tesseract")
    if not tesseract_cmd:
        print("Error: Tesseract OCR engine not found. Please install it (e.g., 'sudo apt install tesseract-ocr').", file=sys.stderr)
        sys.exit(1)

    try:
        # Run tesseract on the image
        # The 'stdout' argument tells tesseract to output to stdout
        result = subprocess.run(
            [tesseract_cmd, resolved_image_path, "stdout"],
            capture_output=True,
            text=True,
            check=True, # Raise CalledProcessError for non-zero exit codes
            encoding='utf-8'
        )
        print(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Error running Tesseract: {e}", file=sys.stderr)
        print(f"Tesseract stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        # This should ideally be caught by shutil.which, but as a fallback
        print("Error: Tesseract command not found. Ensure it's in your PATH.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()