import sys
import json
import os
import glob

# ---------- Argument parsing (template) ----------
raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}
# --------------------------------------------------

def main():
    home = os.path.expanduser("~")
    target_dirs = [
        os.path.join(home, "Pictures", "Screenshots"),
        "/tmp"
    ]
    patterns = ["screenshot*.png", "anuoopt_screenshot.png"]

    files_to_delete = []
    for d in target_dirs:
        for pat in patterns:
            # Use glob with recursive=False; pattern already includes *
            files_to_delete.extend(glob.glob(os.path.join(d, pat)))

    # Ensure we only keep actual files (skip directories, broken symlinks, etc.)
    files_to_delete = [f for f in files_to_delete if os.path.isfile(f)]

    removed_count = 0
    errors = []

    for f in files_to_delete:
        try:
            os.remove(f)
            removed_count += 1
        except Exception as e:
            errors.append({"file": f, "error": str(e)})

    summary = {
        "removed": removed_count,
        "total_matched": len(files_to_delete),
        "errors": errors
    }

    # Print JSON summary
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()