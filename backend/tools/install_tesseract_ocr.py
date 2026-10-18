import sys, json, os
import subprocess

# Rule 1: Parse input
raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}

password = args.get("password")

if not password:
    print("Error: Password not provided.", file=sys.stderr)
    sys.exit(1)

def run_sudo_command(command_parts: list, password: str, description: str = "command"):
    """
    Helper function to run a command with sudo, providing the password via stdin.
    Prints errors to stderr and exits with status 1 on failure.
    """
    try:
        process = subprocess.run(
            ["sudo", "-S"] + command_parts,
            # FIX: When text=True, the input argument must be a string, not bytes.
            # subprocess.run will handle encoding the string to bytes internally.
            input=f"{password}\n",  # Pass password to sudo via stdin as a string
            capture_output=True,
            text=True,  # Capture output as text, and expects input as text
            check=True  # Raise CalledProcessError for non-zero exit codes
        )
        return process.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error during {description}: {e}", file=sys.stderr)
        print(f"STDOUT:\n{e.stdout}", file=sys.stderr)
        print(f"STDERR:\n{e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Command '{command_parts[0]}' not found. Is it installed and in PATH?", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred during {description}: {e}", file=sys.stderr)
        sys.exit(1)

# Step 1: Update package lists to ensure we get the latest version
print("Updating package lists...", file=sys.stderr)
run_sudo_command(["apt-get", "update"], password, "apt-get update")

# Step 2: Install Tesseract OCR
print("Installing Tesseract OCR...", file=sys.stderr)
run_sudo_command(["apt-get", "install", "-y", "tesseract-ocr"], password, "Tesseract OCR installation")

# Rule 5: Print ONLY the clean final result to stdout
print("Tesseract OCR installed successfully.")