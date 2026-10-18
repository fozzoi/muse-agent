import sys, json, os, subprocess, shutil
from pathlib import Path

# Import TesseractNotFoundError for specific error handling.
# If pytesseract isn't installed, TesseractNotFoundError won't exist,
# so we define a dummy exception to avoid NameError later.
try:
    from pytesseract import TesseractNotFoundError
except ImportError:
    class TesseractNotFoundError(RuntimeError):
        pass

# --- Input Parsing Boilerplate ---
raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}

# --- Path Resolution Boilerplate (from rules) ---
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

# --- OCR Helper ---
def _run_ocr_with_pytesseract(image_path: str) -> str:
    """Run OCR using the pytesseract Python wrapper."""
    try:
        from PIL import Image
        import pytesseract
    except ImportError as e:
        raise RuntimeError(
            "Required libraries not installed. Install Pillow and pytesseract: "
            "`pip install pillow pytesseract`"
        ) from e

    try:
        img = Image.open(image_path)
        return pytesseract.image_to_string(img)
    except TesseractNotFoundError as e:
        # Re-raise as a specific RuntimeError to be caught by the outer run_ocr function
        raise RuntimeError("Tesseract OCR engine not found via pytesseract.") from e
    except Exception as e:
        # Catch other PIL/pytesseract errors (e.g., bad image format)
        raise RuntimeError(f"Error during pytesseract OCR processing: {e}") from e

def _run_ocr_with_tesseract_cli(image_path: str) -> str:
    """Fallback: invoke the `tesseract` CLI directly."""
    # Ensure the binary exists
    if not shutil.which("tesseract"):
        raise RuntimeError(
            "Tesseract OCR engine not found. Install it with `sudo apt install tesseract-ocr`."
        )
    # tesseract <input> stdout -l eng (default language)
    result = subprocess.check_output(
        ["tesseract", image_path, "stdout"], stderr=subprocess.STDOUT
    )
    return result.decode("utf-8", errors="replace")

def run_ocr(image_path: str) -> str:
    """
    Runs OCR on the specified image file and returns the extracted text.

    Args:
        image_path (str): The path to the image file.

    Returns:
        str: The text extracted from the image.
    """
    resolved = resolve_path(image_path)

    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Image file not found: {resolved}")
    if not os.path.isfile(resolved):
        raise ValueError(f"Path is not a file: {resolved}")

    # Try the high‑level wrapper first; fall back to CLI if needed.
    try:
        return _run_ocr_with_pytesseract(resolved)
    except RuntimeError as e:
        # If pytesseract failed because Tesseract is missing, try CLI fallback.
        if "Tesseract OCR engine not found via pytesseract." in str(e):
            return _run_ocr_with_tesseract_cli(resolved)
        raise  # Re-raise other RuntimeErrors (e.g., bad image format from pytesseract)
    except Exception as e:
        # Catch any other unexpected errors from _run_ocr_with_pytesseract
        raise # Re-raise

# --- Main Execution ---
if __name__ == "__main__":
    image_file_path = args.get("path")

    if not image_file_path:
        print("Error: 'path' argument is required.", file=sys.stderr)
        sys.exit(1)

    try:
        text = run_ocr(str(image_file_path))
        print(text.strip())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)