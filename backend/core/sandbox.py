import os
import re
import sys
import json
import subprocess
from core.config import Config
from core.router import generate
from core.database import save_tool

CODER_SYSTEM_PROMPT = """You are an autonomous Python Tool Maker for Muse Agent running locally on a user's machine.
Write a standalone Python 3 script that solves the assigned task.

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
            target_path = home_candidate  # Pass resolved candidate to trigger accurate error if missing
```
5. NEVER pass a raw dictionary object to os or shutil functions (e.g. use str(target_path)).
6. Print ONLY the final output to stdout (can be JSON string or clean formatted text).
7. Return ONLY valid Python code inside ```python ... ``` fences. No explanations."""

def _extract_code(raw_text: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", raw_text, re.DOTALL)
    return match.group(1).strip() if match else raw_text.strip()

def run_tool(name: str, args_payload: dict | str = "") -> tuple[bool, str]:
    script_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
    if not os.path.exists(script_path):
        return False, f"Tool '{name}' does not exist."

    if isinstance(args_payload, dict):
        arg_str = json.dumps(args_payload)
    else:
        arg_str = str(args_payload)

    try:
        res = subprocess.run(
            [sys.executable, script_path, arg_str],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode != 0:
            return False, res.stderr.strip() or "Exited with non-zero code."
        return True, res.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "Error: Tool execution timed out after 15 seconds."
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

        success, output = run_tool(name, args_payload)
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
            f"The Python script you wrote failed with this error:\n"
            f"{last_error}\n\n"
            f"Broken code:\n```python\n{code}\n```\n"
            f"Fix the bug. Make sure you extract the path string before passing to os/shutil! Return only Python code in ```python``` fences."
        )

    return {
        "success": False,
        "tool": name,
        "output": f"Failed after {max_retries} attempts. Last error: {last_error}",
    }