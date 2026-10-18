import sys, json, os
import subprocess

# Rule 1: Input parsing
raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}

# Rule 2: Path resolution (not needed for this task)
# def resolve_path(p: str) -> str:
#     if not p:
#         return os.path.expanduser("~")
#     p = os.path.expanduser(str(p).strip())
#     if os.path.isabs(p) and os.path.exists(p):
#         return p
#     for base in [os.path.expanduser("~"), os.path.expanduser("~/Documents"), os.path.expanduser("~/Documents/muse-agent")]:
#         cand = os.path.join(base, p.lstrip("/\"))
#         if os.path.exists(cand):
#             return cand
#     return os.path.join(os.path.expanduser("~"), p.lstrip("/\"))

# Command to launch Zen Browser via Flatpak
command = ["flatpak", "run", "app.zen_browser.zen"]

try:
    # Execute the command, capturing stdout and stderr
    # text=True decodes stdout/stderr as text using default encoding
    process = subprocess.run(command, capture_output=True, text=True, check=False)

    # Print stdout if any
    if process.stdout:
        print(process.stdout.strip())

    # Rule 3: Handle errors
    if process.returncode != 0:
        if process.stderr:
            print(f"Error launching Zen Browser:\n{process.stderr.strip()}", file=sys.stderr)
        else:
            print(f"Command failed with exit code {process.returncode} but no stderr output.", file=sys.stderr)
        sys.exit(1)
    elif process.stderr:
        # If there's stderr but no error code, it might be warnings, print to stderr
        print(f"Warnings/Info from Zen Browser launch:\n{process.stderr.strip()}", file=sys.stderr)

except FileNotFoundError:
    print("Error: 'flatpak' command not found. Please ensure Flatpak is installed and in your PATH.", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"An unexpected error occurred: {e}", file=sys.stderr)
    sys.exit(1)