import os
import re
import sys
import json
import subprocess
from core.config import Config
from core.router import generate
from core.database import save_tool

CODER_SYSTEM_PROMPT = """You are an autonomous Python Tool Maker for Muse Agent running locally on a user's Linux machine.
Write a standalone Python 3 script that solves the assigned task.

SYSTEM & ENVIRONMENT AWARENESS:
1. Muse runs locally on Ubuntu Linux. You have real local filesystem, process, and network access.
2. PREFER Python Standard Library (e.g., urllib.request, json, os, sys, pathlib, shutil, subprocess, sqlite3, csv, re, math, time) whenever possible to avoid unnecessary external dependencies.
3. If an external package is genuinely required (e.g. requests, pillow, beautifulsoup4, pytesseract), use clean standard imports. The runtime has self-healing auto-installation to install missing packages on demand.
4. For GUI / Desktop / Display operations: The user's system runs GNOME on Wayland (Mutter). Standard X11 screen-capture tools (e.g. basic PIL ImageGrab) may return blank frames. Use native utilities (e.g. gnome-screenshot, grim, or inspecting ~/Pictures/Screenshots) when appropriate.

CRITICAL INPUT & PATH RESOLUTION RULES:
1. Input is passed via sys.argv[1]. It could be a JSON string, a plain string, or empty.
2. For tools dealing with files or directories, NEVER default to root ('/'). Enforce home directory expansion (os.path.expanduser) and relative path resolution against '~' or '~/Documents'.
3. Fall back to os.path.expanduser("~/Documents") or os.path.expanduser("~") instead of '/' when the path is omitted or ambiguous.
4. ALWAYS parse input and resolve paths safely like this:
```python
import sys, json, os

raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
target = ""

if raw:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            target = (
                data.get("path")
                or data.get("directory")
                or data.get("folder")
                or data.get("target")
                or ""
            )
        elif isinstance(data, str):
            target = data
    except Exception:
        target = str(raw).strip()

# Path normalization & resolution:
if not target:
    target_path = os.path.expanduser("~/Documents")
else:
    expanded = os.path.expanduser(target)
    if os.path.isabs(expanded) and os.path.exists(expanded):
        target_path = expanded
    else:
        # Check relative to ~ and ~/Documents
        home_candidate = os.path.join(os.path.expanduser("~"), target)
        doc_candidate = os.path.join(os.path.expanduser("~/Documents"), target)
        if os.path.exists(home_candidate):
            target_path = home_candidate
        elif os.path.exists(doc_candidate):
            target_path = doc_candidate
        else:
            target_path = home_candidate  # Pass candidate to trigger accurate error if missing
```
5. NEVER pass a raw dictionary object to os or shutil functions (e.g. use str(target_path)).
6. Wrap risky operations in try-except blocks with meaningful error messages.
7. Print ONLY the final output to stdout (can be JSON string or clean formatted text).
8. Return ONLY valid Python code inside ```python ... ``` fences. No conversational explanations."""

# Map commonly imported module names to their PyPI package names
COMMON_PACKAGE_MAP = {
    "pil": "pillow",
    "cv2": "opencv-python-headless",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "sklearn": "scikit-learn",
    "dateutil": "python-dateutil",
    "git": "gitpython",
    "fitz": "pymupdf",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "serial": "pyserial",
    "magic": "python-magic",
    "jwt": "pyjwt",
    "crypto": "pycryptodome",
    "telegram": "python-telegram-bot",
    "websocket": "websocket-client",
    "dns": "dnspython",
}

def get_python_executable() -> str:
    """Returns the backend virtualenv python if available, else current sys.executable."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_py = os.path.join(backend_dir, "venv", "bin", "python3")
    if os.path.exists(venv_py) and os.access(venv_py, os.X_OK):
        return venv_py
    return sys.executable

def extract_missing_module(error_message: str) -> str | None:
    """Detects missing module names from ModuleNotFoundError or ImportError tracebacks."""
    if not error_message:
        return None
    match = re.search(r"(?:ModuleNotFoundError|ImportError):\s+No module named ['\"]([^'\"]+)['\"]", error_message)
    if match:
        raw_mod = match.group(1).split(".")[0].strip()
        return raw_mod
    return None

def auto_install_package(module_name: str) -> tuple[bool, str]:
    """Self-healing: installs a missing package into the environment via pip."""
    pkg_name = COMMON_PACKAGE_MAP.get(module_name.lower(), module_name)
    python_bin = get_python_executable()
    print(f"[Sandbox Self-Healing] 🔧 Missing module '{module_name}' detected. Auto-installing '{pkg_name}'...", flush=True)
    try:
        res = subprocess.run(
            [python_bin, "-m", "pip", "install", pkg_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if res.returncode == 0:
            print(f"[Sandbox Self-Healing] ✅ Successfully installed '{pkg_name}'!", flush=True)
            return True, f"Installed {pkg_name}"
        else:
            err = res.stderr.strip() or res.stdout.strip()
            print(f"[Sandbox Self-Healing] ❌ Failed to install '{pkg_name}': {err}", flush=True)
            return False, err
    except Exception as e:
        print(f"[Sandbox Self-Healing] ❌ Exception installing '{pkg_name}': {e}", flush=True)
        return False, str(e)

def _extract_code(raw_text: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", raw_text, re.DOTALL)
    return match.group(1).strip() if match else raw_text.strip()

def run_tool(name: str, args_payload: dict | str = "", allow_auto_install: bool = True) -> tuple[bool, str]:
    script_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
    if not os.path.exists(script_path):
        return False, f"Tool '{name}' does not exist."

    if isinstance(args_payload, dict):
        arg_str = json.dumps(args_payload)
    else:
        arg_str = str(args_payload)

    python_bin = get_python_executable()

    try:
        res = subprocess.run(
            [python_bin, script_path, arg_str],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if res.returncode != 0:
            err = res.stderr.strip() or "Exited with non-zero code."
            # Self-healing attempt: if a module is missing, try auto-installing once and re-run
            if allow_auto_install:
                missing_mod = extract_missing_module(err)
                if missing_mod:
                    installed, _ = auto_install_package(missing_mod)
                    if installed:
                        print(f"[Sandbox Self-Healing] 🔄 Re-running tool '{name}' after installing '{missing_mod}'...", flush=True)
                        retry_res = subprocess.run(
                            [python_bin, script_path, arg_str],
                            capture_output=True,
                            text=True,
                            timeout=25,
                        )
                        if retry_res.returncode == 0:
                            return True, retry_res.stdout.strip()
                        err = retry_res.stderr.strip() or err
            return False, err
        return True, res.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "Error: Tool execution timed out after 25 seconds."
    except Exception as e:
        return False, str(e)

async def create_and_run_tool(name: str, description: str, args_payload: dict | str = "", max_retries: int = 3) -> dict:
    arg_str = json.dumps(args_payload) if isinstance(args_payload, dict) else str(args_payload)
    prompt = (
        f"Task description: {description}\n"
        f"Expected sys.argv[1] input: {arg_str}\n"
        f"Write the standalone Python script following the template rules."
    )
    last_error = ""

    for attempt in range(1, max_retries + 1):
        res = await generate("coder", prompt, system=CODER_SYSTEM_PROMPT)
        code = _extract_code(res["text"])

        script_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        # Allow run_tool to auto-heal missing packages if encountered
        success, output = run_tool(name, args_payload, allow_auto_install=True)
        if success:
            await save_tool(name, description, code)
            return {
                "success": True,
                "tool": name,
                "output": output,
                "attempts": attempt,
                "model": f"{res['provider']}/{res['model']}",
            }

        last_error = output
        prompt = (
            f"The Python script you wrote failed with this error on attempt {attempt}/{max_retries}:\n"
            f"{last_error}\n\n"
            f"Broken code:\n```python\n{code}\n```\n"
            f"Diagnose and fix the issue. If standard library alternatives exist, prefer them. Ensure path variables are converted to str before os/shutil calls. Return only Python code in ```python``` fences."
        )

    return {
        "success": False,
        "tool": name,
        "output": f"Failed after {max_retries} attempts. Last error: {last_error}",
    }