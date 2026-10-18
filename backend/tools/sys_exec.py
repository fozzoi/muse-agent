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

    timeout = int(args.get("timeout", 15))

    try:
        res = subprocess.run(
            cmd,
            shell=True,
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
