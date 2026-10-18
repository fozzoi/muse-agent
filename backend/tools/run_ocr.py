import sys, json, os
import pytesseract
from PIL import Image
import shutil

# Rule 1: Parse input JSON
raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}

# Rule 2: Path resolution function
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

# --- DIAGNOSIS & CLARIFICATION ---
# The error "Tesseract-OCR executable not found in your system's PATH."
# is printed by the following block, indicating that `shutil.which("tesseract")`
# returned None. This means the Tesseract-OCR executable is genuinely
# not found in the system's PATH environment variable when the script runs.
#
# The Python code itself is robust in diagnosing this external dependency.
# The actual fix for the reported error is to install Tesseract-OCR on the system.
# On Ubuntu, this is typically done via: sudo apt install tesseract-ocr
#
# No changes are needed to the Python logic for this specific error,
# as it correctly identifies and reports the missing external dependency.
tesseract_executable = shutil.which("tesseract")
if not tesseract_executable:
    print("Error: Tesseract-OCR executable not found in your system's PATH.", file=sys.stderr)
    print("Please install it. On Ubuntu: sudo apt install tesseract-ocr", file=sys.stderr)
    sys.exit(1)

# Set the tesseract command path for pytesseract.
# This ensures pytesseract uses the detected executable and can sometimes prevent issues.
# This line is only reached if tesseract_executable is a valid path string.
pytesseract.pytesseract.tesseract_cmd = tesseract_executable
# --- END DIAGNOSIS & CLARIFICATION ---

# Get the image path from arguments
image_path = args.get("path")

if not image_path:
    print("Error: 'path' argument is required for OCR.", file=sys.stderr)
    sys.exit(1)

resolved_image_path = resolve_path(image_path)

if not os.path.exists(resolved_image_path):
    print(f"Error: Image file not found at '{resolved_image_path}'", file=sys.stderr)
    sys.exit(1)

if not os.path.isfile(resolved_image_path):
    print(f"Error: Path '{resolved_image_path}' is not a file.", file=sys.stderr)
    sys.exit(1)

try:
    # Open the image using Pillow
    img = Image.open(resolved_image_path)

    # Perform OCR using pytesseract
    extracted_text = pytesseract.image_to_string(img)

    # Rule 5: Print only the clean final result to stdout
    print(extracted_text.strip())

except Exception as e:
    # Rule 3: Let exceptions raise naturally or print to stderr and exit 1
    # This catch will handle other potential issues (e.g., image processing errors,
    # unexpected pytesseract internal errors even if the executable is found).
    print(f"An error occurred during OCR: {e}", file=sys.stderr)
    sys.exit(1)