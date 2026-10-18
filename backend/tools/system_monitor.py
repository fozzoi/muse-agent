#!/usr/bin/env python3
"""System Monitor & Proactive Health Tool for Muse Agent (CPU, RAM, Disks, Top Consumers, Uptime, Battery)."""
import os
import sys
import json
import psutil
from datetime import datetime

def get_battery():
    try:
        batt = psutil.sensors_battery()
        if batt:
            return {
                "percent": round(batt.percent, 1),
                "power_plugged": batt.power_plugged,
                "seconds_left": batt.secsleft if batt.secsleft > 0 else "Charging/Full"
            }
    except Exception:
        pass
    return None

def get_top_processes(n: int = 5):
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        try:
            info = p.info
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Sort by memory %
    top_mem = sorted(procs, key=lambda x: x['memory_percent'] or 0, reverse=True)[:n]
    # Sort by cpu %
    top_cpu = sorted(procs, key=lambda x: x['cpu_percent'] or 0, reverse=True)[:n]

    return {
        "top_memory": [
            {"pid": p["pid"], "name": p["name"], "mem_percent": round(p["memory_percent"], 1)}
            for p in top_mem
        ],
        "top_cpu": [
            {"pid": p["pid"], "name": p["name"], "cpu_percent": round(p["cpu_percent"], 1)}
            for p in top_cpu if (p["cpu_percent"] or 0) > 0.0
        ]
    }

def main():
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count(logical=True)
    load1, load5, load15 = os.getloadavg()

    mem = psutil.virtual_memory()
    disk_root = psutil.disk_usage('/')
    disk_home = psutil.disk_usage(os.path.expanduser('~'))

    uptime_secs = int(datetime.now().timestamp() - psutil.boot_time())
    hours, rem = divmod(uptime_secs, 3600)
    minutes, _ = divmod(rem, 60)

    alerts = []
    if mem.percent > 90:
        alerts.append(f"CRITICAL: Memory usage is at {mem.percent}%!")
    elif mem.percent > 80:
        alerts.append(f"WARNING: High memory usage ({mem.percent}%)")

    if disk_root.percent > 90:
        alerts.append(f"CRITICAL: Root disk usage is at {disk_root.percent}%!")

    if cpu_percent > 85:
        alerts.append(f"WARNING: High CPU load ({cpu_percent}%)")

    result = {
        "health_status": "Degraded" if any("CRITICAL" in a for a in alerts) else ("Attention" if alerts else "Healthy"),
        "alerts": alerts,
        "cpu": {
            "overall_usage_percent": cpu_percent,
            "logical_cores": cpu_count,
            "load_averages": [round(load1, 2), round(load5, 2), round(load15, 2)]
        },
        "memory": {
            "total_gb": round(mem.total / (1024**3), 2),
            "used_gb": round(mem.used / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
            "percent": mem.percent
        },
        "disks": {
            "root": {
                "total_gb": round(disk_root.total / (1024**3), 2),
                "free_gb": round(disk_root.free / (1024**3), 2),
                "percent": disk_root.percent
            },
            "home": {
                "total_gb": round(disk_home.total / (1024**3), 2),
                "free_gb": round(disk_home.free / (1024**3), 2),
                "percent": disk_home.percent
            }
        },
        "battery": get_battery(),
        "uptime": f"{hours}h {minutes}m",
        "top_consumers": get_top_processes(5)
    }

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
