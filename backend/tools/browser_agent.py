#!/usr/bin/env python3
"""Autonomous Browser Agent for Muse Agent.
Navigates the web, extracts page content, captures screenshots, clicks/types, and maintains persistent authenticated logins via Playwright and system Google Chrome.
"""
import os
import sys
import json
import time
import subprocess
from bs4 import BeautifulSoup

PROFILE_DIR = os.path.expanduser("~/.config/muse-agent/browser-profile")
SCREENSHOTS_DIR = os.path.expanduser("~/Pictures/Screenshots")

def get_env():
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env

def parse_args():
    raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"url": str(raw)}
    except Exception:
        return {"url": str(raw)}

def clean_html(html_content: str) -> str:
    soup = BeautifulSoup(html_content, "html.parser")
    # Remove script, style, noscript, svg, footer tags
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:120])  # Cap at top 120 readable lines

def main():
    args = parse_args()
    os.makedirs(PROFILE_DIR, exist_ok=True)
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    action = str(args.get("action", "navigate")).lower().strip()
    url = str(args.get("url") or args.get("link") or "").strip()

    # Handle desktop interactive login session
    if action in ("login_session", "open_headed", "login", "gui"):
        target_url = url or "https://www.google.com"
        if not target_url.startswith(("http://", "https://")):
            target_url = f"https://{target_url}"
        try:
            subprocess.Popen(
                ["google-chrome", f"--user-data-dir={PROFILE_DIR}", target_url],
                env=get_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print(json.dumps({
                "status": "success",
                "action": "login_session",
                "url": target_url,
                "message": f"Opened Google Chrome on your desktop with your persistent profile. Log in to {target_url}. Once logged in, close the tab—your cookies and sessions are permanently saved for future automated tasks!"
            }, indent=2))
        except Exception as e:
            print(json.dumps({"error": f"Failed to launch Chrome GUI: {e}"}))
        return

    # Headless Playwright Automation
    from playwright.sync_api import sync_playwright

    if not url:
        # Default fallback if no URL was given
        url = "https://news.ycombinator.com"

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    shot_filename = f"web_{int(time.time())}.png"
    shot_path = os.path.join(SCREENSHOTS_DIR, shot_filename)

    result = {
        "url": url,
        "action": action
    }

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                executable_path="/usr/bin/google-chrome",
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--window-size=1280,800",
                    "--disable-blink-features=AutomationControlled"
                ],
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            page.set_default_timeout(25000)

            # Navigate
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(1.0)  # Brief wait for client-side hydration

            # Optional Click action
            click_sel = args.get("click_selector") or args.get("selector")
            if action in ("click", "interact") and click_sel:
                try:
                    page.click(click_sel, timeout=5000)
                    time.sleep(1.0)
                    result["clicked_selector"] = click_sel
                except Exception as e:
                    result["click_warning"] = str(e)

            # Optional Type action
            type_text = args.get("text") or args.get("type_text")
            if action in ("type", "fill") and click_sel and type_text:
                try:
                    page.fill(click_sel, type_text, timeout=5000)
                    submit = args.get("submit", False)
                    if submit:
                        page.keyboard.press("Enter")
                        time.sleep(2.0)
                    result["typed_into"] = click_sel
                except Exception as e:
                    result["type_warning"] = str(e)

            # Capture Page Info
            title = page.title()
            current_url = page.url
            html = page.content()
            readable_text = clean_html(html)

            # Always capture screenshot for delivery
            page.screenshot(path=shot_path, full_page=False)

            result["title"] = title
            result["current_url"] = current_url
            result["text_content"] = readable_text[:3000]
            result["file_path"] = shot_path

            context.close()

        print(json.dumps(result, indent=2))

    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "url": url,
            "fallback_hint": "If this site blocks headless automated requests, use action='login_session' to open it directly in your persistent browser profile on the desktop."
        }, indent=2))

if __name__ == "__main__":
    main()
