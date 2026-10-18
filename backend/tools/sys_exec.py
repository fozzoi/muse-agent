#!/usr/bin/env python3
"""Safe Shell Command Execution Primitive for Muse Agent."""
import os
import sys
import json
import subprocess

FORBIDDEN_PATTERNS = [
    "rm -rf /",
    ":(){ :|:& };:",
    "> /dev/sda",
    "mkfs",
    "dd if=",
]

def parse_args():
    raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"command": str(raw)}
    except Exception:
        return {"command": str(raw)}

def main():
    args = parse_args()
    cmd = args.get("command") or args.get("cmd") or ""
    if not cmd:
        print(json.dumps({"error": "No command provided to sys_exec."}))
        return

    for forbidden in FORBIDDEN_PATTERNS:
        if forbidden in cmd:
            print(json.dumps({"error": f"Command rejected: contains forbidden pattern '{forbidden}'"}))
            return

    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_bin = os.path.join(backend_dir, "venv", "bin")
    if os.path.isdir(venv_bin):
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = os.path.join(backend_dir, "venv")

    timeout = int(args.get("timeout", 60))

    try:
        res = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=os.path.expanduser("~")
        )

        # Self-healing hook for inline python execution:
        if res.returncode != 0:
            combined_err = f"{res.stderr}\n{res.stdout}"
            import re
            m = re.search(r"(?:ModuleNotFoundError|ImportError):\s+No module named ['\"]([^'\"]+)['\"]", combined_err)
            if not m:
                m = re.search(r"No module named ['\"]([^'\"]+)['\"]", combined_err)
            if m:
                missing_pkg = m.group(1).split(".")[0].strip()
                backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                venv_py = os.path.join(backend_dir, "venv", "bin", "python3")
                py_bin = venv_py if os.path.exists(venv_py) else sys.executable
                print(f"[sys_exec Self-Healing] Auto-installing missing package '{missing_pkg}'...", file=sys.stderr)
                pip_res = subprocess.run([py_bin, "-m", "pip", "install", missing_pkg], capture_output=True, timeout=120)
                if pip_res.returncode == 0:
                    # Retry the command!
                    res = subprocess.run(
                        cmd,
                        shell=True,
                        executable="/bin/bash",
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        env=env,
                        cwd=os.path.expanduser("~")
                    )

        output = {
            "exit_code": res.returncode,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip()
        }
        print(json.dumps(output, indent=2))
    except subprocess.TimeoutExpired:
        print(json.dumps({"error": f"Command timed out after {timeout} seconds.", "exit_code": 124}))
    except Exception as e:
        print(json.dumps({"error": str(e), "exit_code": 1}))

if __name__ == "__main__":
    main()
