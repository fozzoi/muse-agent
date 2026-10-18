import os
import sys
import platform
import shutil
import json

def get_system_info():
    # OS
    os_info = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": platform.machine(),
    }

    # CPU
    cpu_cores = os.cpu_count() or 1

    # RAM
    ram = {}
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = parts[1].strip()
            total_kb = int(meminfo.get("MemTotal", "0 kB").split()[0])
            avail_kb = int(meminfo.get("MemAvailable", "0 kB").split()[0])
            used_kb = total_kb - avail_kb
            ram = {
                "total_gb": round(total_kb / 1024 / 1024, 2),
                "used_gb": round(used_kb / 1024 / 1024, 2),
                "available_gb": round(avail_kb / 1024 / 1024, 2),
                "percent_used": round((used_kb / total_kb) * 100, 1) if total_kb else 0,
            }
    except Exception as e:
        ram = {"error": str(e)}

    # Disk
    total, used, free = shutil.disk_usage("/")
    disk = {
        "total_gb": round(total / (1024**3), 2),
        "used_gb": round(used / (1024**3), 2),
        "free_gb": round(free / (1024**3), 2),
        "percent_used": round((used / total) * 100, 1),
    }

    # Uptime
    uptime_str = "unknown"
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
            hrs, rem = divmod(int(uptime_seconds), 3600)
            mins, secs = divmod(rem, 60)
            uptime_str = f"{hrs}h {mins}m {secs}s"
    except Exception:
        pass

    return {
        "os": os_info,
        "cpu": {"cores": cpu_cores},
        "ram": ram,
        "disk_root": disk,
        "uptime": uptime_str,
    }

if __name__ == "__main__":
    print(json.dumps(get_system_info(), indent=2))
