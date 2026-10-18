#!/usr/bin/env python3
"""Media and Device Control Tool for Muse Agent (Audio volume, mute, WirePlumber sinks, and Bluetooth status)."""
import os
import sys
import json
import subprocess

def get_env():
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env

def run_cmd(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=get_env())
        return res.returncode, (res.stdout or res.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "Command timed out"
    except Exception as e:
        return 1, str(e)

def parse_args():
    raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"action": str(raw)}
    except Exception:
        return {"action": str(raw)}

def main():
    args = parse_args()
    action = str(args.get("action", "status")).lower().strip()
    val = str(args.get("value", "")).strip()

    result = {}

    # Audio Volume & Mute actions via WirePlumber (wpctl)
    if "up" in action or "increase" in action:
        step = val if val else "5%+"
        if not step.endswith("+") and not step.endswith("%+"):
            step = f"{step}+" if "%" in step else f"{step}%+"
        run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", step])
        _, vol = run_cmd(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        result["message"] = f"Increased volume. Current: {vol}"

    elif "down" in action or "decrease" in action or "lower" in action:
        step = val if val else "5%-"
        if not step.endswith("-") and not step.endswith("%-"):
            step = f"{step}-" if "%" in step else f"{step}%-"
        run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", step])
        _, vol = run_cmd(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        result["message"] = f"Decreased volume. Current: {vol}"

    elif "set" in action or "volume" in action and val:
        # e.g., 50% or 0.5
        v = val.replace("%", "").strip()
        try:
            num = float(v)
            if num > 1.0:
                num = num / 100.0
            run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{num:.2f}"])
            _, vol = run_cmd(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            result["message"] = f"Set volume to {num*100:.0f}%. Current: {vol}"
        except ValueError:
            result["error"] = f"Invalid volume value: {val}"

    elif "mute" in action or "unmute" in action or "toggle" in action:
        run_cmd(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
        _, vol = run_cmd(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        result["message"] = f"Audio mute toggled. Current: {vol}"

    elif "bluetooth" in action or "bt" in action:
        _, bt_show = run_cmd(["bluetoothctl", "show"])
        _, bt_devs = run_cmd(["bluetoothctl", "devices"])
        result["bluetooth"] = {
            "controller": bt_show.split("\n")[0] if bt_show else "Unknown",
            "powered": "yes" if "Powered: yes" in bt_show else "no",
            "devices": bt_devs.splitlines() if bt_devs else []
        }

    else:
        # Default: full media status
        _, vol = run_cmd(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        _, bt_show = run_cmd(["bluetoothctl", "show"])
        _, bt_devs = run_cmd(["bluetoothctl", "devices"])
        _, wp_status = run_cmd(["wpctl", "status"])

        # extract default sink line if possible
        sink_name = "Default Sink"
        for line in wp_status.splitlines():
            if "*" in line and ("Sink" in line or "Digital" in line or "Stereo" in line):
                sink_name = line.strip()
                break

        result["audio"] = {
            "default_sink": sink_name,
            "volume_status": vol or "Unknown"
        }
        result["bluetooth"] = {
            "powered": "yes" if "Powered: yes" in bt_show else "no",
            "device_count": len(bt_devs.splitlines()) if bt_devs else 0
        }

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
