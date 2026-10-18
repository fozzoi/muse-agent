import os
import re
import sys
import json
import subprocess
from core.config import Config
from core.router import generate
from core.database import save_tool
from core.journal import record_lesson, get_journal_context

CODER_SYSTEM_PROMPT = """You are an elite Linux & Python Systems Engineer building standalone CLI tools for Muse Agent on Ubuntu Linux (GNOME/Wayland/PipeWire).

RULES FOR WRITING TOOLS:
1. Input is passed as a JSON string in `sys.argv[1]`. Parse it cleanly:
```python
import sys, json, os

raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}
```

2. ONLY apply path resolution IF the task is specifically about files or directories:
```python
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
```

3. SAFE DEFAULTS (NO CRASH ON EMPTY ARGS):
Your script MUST execute cleanly when called with empty input `{}`. Provide sensible default values for all parameters. NEVER call `sys.exit(1)` or crash with KeyError simply because an argument key wasn't passed.
If no parameters are provided, perform default status reporting or return a clean JSON error response without non-zero exit codes.

4. NO INTERACTIVE SUDO:
Never call commands requiring interactive root/sudo password (like `sudo apt`). If a tool needs elevated privileges, report it in the JSON output cleanly.

5. For Linux system tasks:
- Audio/Media: Use wpctl or pactl via subprocess.check_output. Ensure env sets XDG_RUNTIME_DIR=/run/user/<uid> and DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus.
- Hardware/System: Read /proc, /sys, shutil, psutil, or standard CLI utilities (df, free, ip, lsblk, upower).

6. Print ONLY the clean final result (JSON or clear text) to stdout.
7. Return ONLY valid Python code inside ```python ... ``` fences."""

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
    "pyscreenshot": "pyscreenshot",
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

    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")

    try:
        res = subprocess.run(
            [python_bin, script_path, arg_str],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        if res.returncode != 0:
            err = (
                f"{res.stderr.strip()}\n{res.stdout.strip()}".strip()
                or "Exited with non-zero code."
            )
            # Self-healing attempt: if a module is missing, try auto-installing once and re-run
            if allow_auto_install:
                missing_mod = extract_missing_module(err)
                if missing_mod:
                    installed, _ = auto_install_package(missing_mod)
                    if installed:
                        print(f"[Sandbox Self-Healing] 🔄 Re-running tool '{name}' after installing '{missing_mod}'...", flush=True)
                        retry_res = subprocess.run(
                            [python_bin, script_path, arg_str],
                            env=env,
                            capture_output=True,
                            text=True,
                            timeout=25,
                        )
                        if retry_res.returncode == 0:
                            retry_out = retry_res.stdout.strip()
                            if not (
                                retry_out.startswith("ERROR:")
                                or retry_out.startswith("Traceback (most recent call last):")
                                or "Traceback (most recent call last):" in retry_out
                            ):
                                return True, retry_out
                        err = (
                            f"{retry_res.stderr.strip()}\n{retry_res.stdout.strip()}".strip()
                            or err
                        )
            return False, err

        out = res.stdout.strip()
        # Fail tools that caught their own exceptions or printed tracebacks
        if (
            out.startswith("ERROR:")
            or out.startswith("Traceback (most recent call last):")
            or "Traceback (most recent call last):" in out
        ):
            return False, out

        return True, out
    except subprocess.TimeoutExpired:
        return False, "Error: Tool execution timed out after 25 seconds."
    except Exception as e:
        return False, str(e)

async def create_and_run_tool(name: str, description: str, args_payload: dict | str = "", max_retries: int = 3) -> dict:
    arg_str = json.dumps(args_payload) if isinstance(args_payload, dict) else str(args_payload)
    lessons = get_journal_context(f"{name} {description}")
    prompt = (
        f"Task description: {description}\n"
        f"Expected sys.argv[1] input: {arg_str}\n"
        f"Write the standalone Python script following the template rules."
    )
    if lessons:
        prompt += f"\n\nLEARNED SYSTEM HEURISTICS FROM JOURNAL (Avoid known pitfalls):\n{lessons}\n"

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
            if attempt > 1 and last_error:
                record_lesson(
                    title=f"Fix for {name}",
                    problem=last_error[:200],
                    solution=f"Successfully fixed and executed on attempt {attempt}.",
                    tags=[name, "auto_healed"]
                )
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