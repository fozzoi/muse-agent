import os
import re
import sys
import json
import subprocess
from core.config import Config
from core.router import generate
from core.database import save_tool
from core.journal import record_lesson, get_journal_context

CODER_SYSTEM_PROMPT = """You are an elite Linux & Python Systems Engineer building standalone CLI tools and tool packages for Muse Agent on Ubuntu Linux (GNOME/Wayland/PipeWire).

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

5. SCREENSHOT RULE FOR UBUNTU GNOME / WAYLAND:
NEVER call 'grim' or 'slurp'. grim is only for wlroots/Sway and fails on Ubuntu GNOME.
Use either python mss, pyscreenshot, or pillow ImageGrab:
```python
import mss
with mss.mss() as sct:
    sct.shot(output=str(output_path))
```
Or invoke the existing local tool 'take_screenshot.py'.

6. DESKTOP NOTIFICATIONS:
The GNOME notification server does not implement GetNotifications. Do not call org.freedesktop.Notifications.GetNotifications.
To check notifications, inspect system logs via 'journalctl -b --user -u org.gnome.Shell' or read app-specific state.

7. LINUX SYSTEM TASKS:
- Audio/Media: Use wpctl or pactl via subprocess.check_output. Ensure env sets XDG_RUNTIME_DIR=/run/user/<uid> and DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus.
- Hardware/System: Read /proc, /sys, shutil, psutil, or standard CLI utilities (df, free, ip, lsblk, upower).

8. MULTI-FILE & PACKAGE TOOLS:
For complex tools requiring modular architecture (submodules, scrapers, data models, helpers), you can structure the tool across multiple files using:
### FILE: main.py
```python
import sys, json, os
from helper import helper_fn
...
```
### FILE: helper.py
```python
def helper_fn():
    ...
```
The primary entrypoint MUST always be named `main.py`.

9. MULTI-FILE & FOLDER OUTPUT GENERATION:
If your tool generates files, charts, scraped assets, downloads, or datasets:
- Save them in a dedicated output directory:
```python
output_dir = os.path.expanduser(f"~/Documents/muse-agent/backend/data/outputs/{tool_name}")
os.makedirs(output_dir, exist_ok=True)
```
- Print a clean JSON summary object to stdout:
```json
{
  "status": "success",
  "output_dir": "/path/to/generated/folder",
  "files": ["chart.png", "report.json"],
  "count": 2,
  "summary": "Generated 2 files successfully."
}
```

10. Output Format:
Print ONLY the clean final result (JSON or clear text) to stdout.
Return code inside ```python ... ``` fences or ### FILE: blocks."""

# Map commonly imported module names to their PyPI package names
COMMON_PACKAGE_MAP = {
    "pil": "pillow",
    "pillow": "pillow",
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
    "mss": "mss",
    "instaloader": "instaloader",
    "playwright": "playwright",
    "pyautogui": "pyautogui",
}

def get_python_executable() -> str:
    """Returns the backend virtualenv python if available, else current sys.executable."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_py = os.path.join(backend_dir, "venv", "bin", "python3")
    if os.path.exists(venv_py) and os.access(venv_py, os.X_OK):
        return venv_py
    return sys.executable

def extract_missing_module(error_message: str) -> str | None:
    """Detects missing module names from ModuleNotFoundError, ImportError, or raw tracebacks."""
    if not error_message:
        return None
    patterns = [
        r"(?:ModuleNotFoundError|ImportError):\s+No module named ['\"]([^'\"]+)['\"]",
        r"No module named ['\"]([^'\"]+)['\"]",
        r"cannot import name ['\"][^'\"]+['\"] from ['\"]([^'\"]+)['\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, error_message)
        if match:
            raw_mod = match.group(1).split(".")[0].strip()
            if raw_mod and not raw_mod.startswith("_"):
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

def _extract_files_and_code(raw_text: str, default_name: str) -> dict[str, str]:
    """
    Extracts multiple files if structured with '### FILE: <filename>' or similar markers.
    Falls back to a single file '{default_name}.py' from the main code block.
    Returns: dict mapping filename -> code_content
    """
    files = {}

    file_pattern = r"(?:^|\n)(?:###?\s*(?:FILE:?|File:?)\s*|\*\*File:?\*\*\s*|#\s*filename:\s*)([^\n\r`]+)\n```(?:python|json|yaml|txt|sh)?\s*(.*?)```"
    matches = list(re.finditer(file_pattern, raw_text, re.DOTALL | re.IGNORECASE))

    if matches:
        for match in matches:
            raw_path = match.group(1).strip().strip("'\"` ")
            code = match.group(2).strip()
            if raw_path and code:
                clean_name = os.path.basename(raw_path)
                files[clean_name] = code

    # Fallback to single code block
    if not files:
        match = re.search(r"```(?:python)?\s*(.*?)```", raw_text, re.DOTALL)
        code = match.group(1).strip() if match else raw_text.strip()
        files[f"{default_name}.py"] = code

    return files

def _extract_code(raw_text: str) -> str:
    """Backward-compatible single block extractor."""
    files = _extract_files_and_code(raw_text, "custom_tool")
    if "main.py" in files:
        return files["main.py"]
    return list(files.values())[0] if files else raw_text.strip()

def run_tool(name: str, args_payload: dict | str = "", allow_auto_install: bool = True) -> tuple[bool, str]:
    script_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
    pkg_entry = os.path.join(Config.TOOLS_DIR, name, "main.py")

    # If launcher doesn't exist but package main.py does, run package main.py directly
    if not os.path.exists(script_path) and os.path.exists(pkg_entry):
        script_path = pkg_entry
    elif not os.path.exists(script_path):
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

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_bin = os.path.join(backend_dir, "venv", "bin")
    if os.path.isdir(venv_bin):
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = os.path.join(backend_dir, "venv")

    try:
        res = subprocess.run(
            [python_bin, script_path, arg_str],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
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
                            timeout=60,
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
        return False, "Error: Tool execution timed out after 60 seconds."
    except Exception as e:
        return False, str(e)

async def create_and_run_tool(name: str, description: str, args_payload: dict | str = "", max_retries: int = 3) -> dict:
    arg_str = json.dumps(args_payload) if isinstance(args_payload, dict) else str(args_payload)
    lessons = get_journal_context(f"{name} {description}")
    prompt = (
        f"Task description: {description}\n"
        f"Expected sys.argv[1] input: {arg_str}\n"
        f"Write the standalone Python script or package following the template rules.\n"
        f"For modular or complex tasks, structure your output using '### FILE: <filename>' markers."
    )
    if lessons:
        prompt += f"\n\nLEARNED SYSTEM HEURISTICS FROM JOURNAL (Avoid known pitfalls):\n{lessons}\n"

    last_error = ""

    for attempt in range(1, max_retries + 1):
        res = await generate("coder", prompt, system=CODER_SYSTEM_PROMPT)
        files = _extract_files_and_code(res["text"], name)

        is_multi_file = len(files) > 1 or not any(k in (f"{name}.py", "main.py") for k in files.keys())

        if not is_multi_file and len(files) == 1:
            # Single file tool
            code = list(files.values())[0]
            script_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
            saved_main_code = code
        else:
            # Multi-file package tool
            pkg_dir = os.path.join(Config.TOOLS_DIR, name)
            os.makedirs(pkg_dir, exist_ok=True)
            init_file = os.path.join(pkg_dir, "__init__.py")
            if not os.path.exists(init_file):
                with open(init_file, "w", encoding="utf-8") as f:
                    f.write("# Package auto-generated by Muse\n")

            main_code = ""
            for fname, fcontent in files.items():
                target_path = os.path.join(pkg_dir, fname)
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(fcontent)
                if fname in ("main.py", f"{name}.py"):
                    main_code = fcontent

            # Ensure entrypoint main.py exists
            main_path = os.path.join(pkg_dir, "main.py")
            if not os.path.exists(main_path):
                first_py = [k for k in files.keys() if k.endswith(".py")]
                if first_py:
                    main_code = files[first_py[0]]
                    with open(main_path, "w", encoding="utf-8") as f:
                        f.write(main_code)

            # Generate top-level launcher tools/<name>.py
            launcher_code = f'''"""Launcher for package tool: {name}"""
import os, sys, runpy

_pkg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "{name}")
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

_entry = os.path.join(_pkg_dir, "main.py")
if __name__ == "__main__":
    if os.path.exists(_entry):
        runpy.run_path(_entry, run_name="__main__")
    else:
        sys.stderr.write("Entrypoint main.py missing in {name}\\n")
        sys.exit(1)
'''
            launcher_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
            with open(launcher_path, "w", encoding="utf-8") as f:
                f.write(launcher_code)
            saved_main_code = main_code or launcher_code

        # Allow run_tool to execute and auto-heal missing packages if encountered
        success, output = run_tool(name, args_payload, allow_auto_install=True)
        if success:
            await save_tool(name, description, saved_main_code)
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
                "is_package": is_multi_file,
                "files_count": len(files),
            }

        last_error = output
        if is_multi_file:
            broken_snippet = "\n\n".join([f"### FILE: {fn}\n```python\n{fc}\n```" for fn, fc in files.items()])
        else:
            broken_snippet = f"```python\n{saved_main_code}\n```"

        prompt = (
            f"The Python script you wrote failed with this error on attempt {attempt}/{max_retries}:\n"
            f"{last_error}\n\n"
            f"Broken code:\n{broken_snippet}\n\n"
            f"Diagnose and fix the issue. If standard library alternatives exist, prefer them. Ensure path variables are converted to str before os/shutil calls. Return valid Python code inside ```python``` fences or ### FILE: blocks."
        )

    return {
        "success": False,
        "tool": name,
        "output": f"Failed after {max_retries} attempts. Last error: {last_error}",
    }
