import sys, json, os
import subprocess

# Standard argument parsing template
raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}

# No path resolution needed for this task as it's a system command

try:
    # The 'installed' column is not a valid column for `flatpak list`.
    # Common columns include 'application', 'branch', 'version', 'origin', 'arch', 'installed-size'.
    # Removing 'installed' to fix the error.
    command = ["flatpak", "list", "--app", "--columns=application,branch,version"]
    
    # Capture the output
    result = subprocess.check_output(command, text=True, stderr=subprocess.PIPE)
    
    # Print the output to stdout
    print(result.strip())

except FileNotFoundError:
    print("Error: 'flatpak' command not found. Please ensure Flatpak is installed and in your PATH.", file=sys.stderr)
    sys.exit(1)
except subprocess.CalledProcessError as e:
    print(f"Error executing flatpak command: {e}", file=sys.stderr)
    print(f"Command: {' '.join(e.cmd)}", file=sys.stderr)
    print(f"Return Code: {e.returncode}", file=sys.stderr)
    print(f"Output: {e.stdout}", file=sys.stderr)
    print(f"Error Output: {e.stderr}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"An unexpected error occurred: {e}", file=sys.stderr)
    sys.exit(1)