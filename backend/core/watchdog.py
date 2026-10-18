"""Proactive Background Watchdog for Muse Agent.
Periodically monitors system health, resources (RAM, CPU, Disks), and delivers proactive Telegram alerts.
"""
import os
import asyncio
import psutil
from datetime import datetime
from telegram import Bot
from core.config import Config

class SystemWatchdog:
    def __init__(self, check_interval_secs: int = 300):
        self.interval = check_interval_secs
        self.running = False
        self._last_alert_time: dict[str, float] = {}

    def _should_throttle(self, alert_key: str, cooldown_secs: int = 3600) -> bool:
        """Throttles recurring alerts so the user isn't spammed more than once per hour."""
        now = datetime.now().timestamp()
        last = self._last_alert_time.get(alert_key, 0)
        if now - last > cooldown_secs:
            self._last_alert_time[alert_key] = now
            return False
        return True

    async def check_health(self) -> list[str]:
        alerts = []
        # 1. RAM Check
        mem = psutil.virtual_memory()
        if mem.percent > 90 and not self._should_throttle("ram_critical"):
            alerts.append(f"🚨 **Critical Memory Alert**: RAM usage is at `{mem.percent}%` (Only `{round(mem.available / (1024**3), 2)} GB` free)!")
        elif mem.percent > 85 and not self._should_throttle("ram_warn", cooldown_secs=7200):
            alerts.append(f"⚠️ **High Memory Warning**: RAM usage is at `{mem.percent}%`.")

        # 2. Root Disk Check
        disk = psutil.disk_usage('/')
        if disk.percent > 90 and not self._should_throttle("disk_critical"):
            alerts.append(f"🚨 **Critical Disk Alert**: Root drive (`/`) is at `{disk.percent}%` full (Only `{round(disk.free / (1024**3), 2)} GB` free)!")

        # 3. CPU Sustained Spike
        cpu = psutil.cpu_percent(interval=1.0)
        if cpu > 95 and not self._should_throttle("cpu_spike"):
            alerts.append(f"⚠️ **High CPU Alert**: Host CPU load is at `{cpu}%`.")

        return alerts

    async def run(self, bot: Bot, allowed_users: list[str]):
        self.running = True
        print(f"[Watchdog] Proactive background watchdog started (checking every {self.interval}s)", flush=True)

        while self.running:
            try:
                await asyncio.sleep(self.interval)
                alerts = await self.check_health()
                if alerts and allowed_users:
                    msg = "\n\n".join(alerts)
                    for user_id in allowed_users:
                        if user_id.isdigit():
                            try:
                                await bot.send_message(chat_id=int(user_id), text=msg, parse_mode="Markdown")
                                print(f"[Watchdog] Proactive alert dispatched to user {user_id}", flush=True)
                            except Exception as e:
                                print(f"[Watchdog] Failed to send alert to {user_id}: {e}", flush=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Watchdog] Error in monitoring cycle: {e}", flush=True)

watchdog = SystemWatchdog(check_interval_secs=300)
