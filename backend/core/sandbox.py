import os
import re
import sys
import json
import subprocess
from core.config import Config
from core.router import generate
from core.database import save_tool

CODER_SYSTEM_PROMPT = """You are an autonomous Python Tool Maker.
Write a standalone Python 3 script that solves the assigned task.

CRITICAL INPUT RULES:
1. Input is passed via sys.argv[1]. It could be a JSON string, a plain string, or empty.
2. ALWAYS parse input safely like this:
```python
import sys, json, os, shutil

raw_input = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
target = "/"  # sensible fallback default

if raw_input:
    try:
        data = json.loads(raw_input)
        if isinstance(data, dict):
            # Extract first non-empty string value or specific keys
            target = data.get("path") or data.get("mount") or data.get("directory") or next((str(v) for v in data.values() if isinstance(v, str)), "/")
        elif isinstance(data, str):
            target = data
    except Exception:
        target = str(raw_input)
```
3. NEVER pass a dictionary object to os or shutil functions (e.g. use shutil.disk_usage(str(target)), NOT shutil.disk_usage(args)).
4. Print ONLY the final output to stdout (can be JSON string or clean formatted text).
5. Return ONLY valid Python code inside ```python ... ``` fences. No explanations."""

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