#!/usr/bin/env python3
"""Autonomous Browser Agent for Muse Agent.
Navigates the web, extracts page content, captures screenshots, clicks/types, and performs 100% HEADLESS logins (without opening visible windows) or optional desktop GUI sessions.
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

def clean_locks():
    """Removes stale Singleton locks left by previous crashes so Playwright doesn't hang."""
    for item in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = os.path.join(PROFILE_DIR, item)
        if os.path.islink(p) or os.path.exists(p):
            try:
                os.unlink(p)
            except Exception:
                pass

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
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:120])

def main():
    args = parse_args()
    os.makedirs(PROFILE_DIR, exist_ok=True)
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    clean_locks()

    action = str(args.get("action", "navigate")).lower().strip()
    url = str(args.get("url") or args.get("link") or "").strip()
    username = str(args.get("username") or args.get("user") or args.get("email") or "").strip()
    password = str(args.get("password") or args.get("pass") or "").strip()

    # ONLY open visible desktop GUI window if explicitly requested by user
    if action in ("open_window", "open_headed", "gui", "desktop_window"):
        target_url = url or "https://www.instagram.com/"
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
                "action": "open_window",
                "url": target_url,
                "message": f"Opened Google Chrome window on your desktop with persistent profile at {target_url}."
            }, indent=2))
        except Exception as e:
            print(json.dumps({"error": f"Failed to launch Chrome GUI: {e}"}))
        return

    # Headless Automation (Default for ALL operations including logins)
    from playwright.sync_api import sync_playwright

    # Auto-detect login action if credentials are provided
    if (username and password) or action in ("login", "headless_login"):
        action = "login"
        if not url:
            url = "https://www.instagram.com/accounts/login/"

    if not url:
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
            page.set_default_timeout(30000)

            # Navigate
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(2.0)

            # 1. HEADLESS LOGIN FLOW (Zero Windows Opened)
            if action == "login" and username and password:
                user_selectors = [
                    'input[name="email"]',
                    'input[name="username"]',
                    'input[type="email"]',
                    'input[type="text"]',
                    'input[autocomplete="username"]',
                ]
                pass_selectors = [
                    'input[name="pass"]',
                    'input[name="password"]',
                    'input[type="password"]',
                    'input[autocomplete="current-password"]',
                ]
                submit_selectors = [
                    'button[type="submit"]',
                    'input[type="submit"]',
                    'form button',
                    '[role="button"]:has-text("Log In")',
                    '[role="button"]:has-text("Sign in")',
                ]

                # Accept cookies dialog if present
                for cookie_btn in ('button:has-text("Allow all cookies")', 'button:has-text("Accept")', 'button:has-text("Decline optional cookies")'):
                    try:
                        if page.locator(cookie_btn).first.is_visible():
                            page.locator(cookie_btn).first.click(timeout=2000)
                            time.sleep(1.0)
                            break
                    except Exception:
                        pass

                # Fill username
                filled_user = False
                for sel in user_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible():
                            loc.click()
                            loc.fill(username)
                            filled_user = True
                            break
                    except Exception:
                        continue

                # Fill password
                filled_pass = False
                for sel in pass_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible():
                            loc.click()
                            loc.fill(password)
                            filled_pass = True
                            break
                    except Exception:
                        continue

                # Click Submit
                submitted = False
                if filled_user and filled_pass:
                    for sel in submit_selectors:
                        try:
                            loc = page.locator(sel).first
                            if loc.is_visible():
                                loc.click()
                                submitted = True
                                break
                        except Exception:
                            continue

                    if not submitted:
                        page.keyboard.press("Enter")
                        submitted = True

                    # Wait for navigation/redirection after login
                    time.sleep(5.0)

                result["login_attempted"] = True
                result["username_filled"] = filled_user
                result["password_filled"] = filled_pass
                result["submitted"] = submitted

                # Analyze login status
                page_text_lower = page.inner_text("body").lower()
                if any(x in page_text_lower for x in ("security code", "two-factor", "verification code", "enter the 6-digit")):
                    result["login_status"] = "2FA_REQUIRED"
                    result["notice"] = "Instagram is asking for a 2FA or verification code. See attached screenshot."
                elif any(x in page_text_lower for x in ("incorrect password", "the password that you've entered is incorrect", "sorry, your password was incorrect")):
                    result["login_status"] = "INVALID_PASSWORD"
                    result["notice"] = "Instagram reported the password was incorrect. See attached screenshot."
                elif "/login" not in page.url and any(x in page_text_lower for x in ("home", "search", "explore", "reels", "messages", "profile")):
                    result["login_status"] = "SUCCESS"
                    result["notice"] = "Successfully logged in headlessly! Session cookies saved permanently."
                else:
                    result["login_status"] = "CHALLENGE_OR_PENDING"
                    result["notice"] = f"Login submitted. Current page URL: {page.url}"

            # 2. General Click action
            click_sel = args.get("click_selector") or args.get("selector")
            if action in ("click", "interact") and click_sel:
                try:
                    page.click(click_sel, timeout=5000)
                    time.sleep(1.0)
                    result["clicked_selector"] = click_sel
                except Exception as e:
                    result["click_warning"] = str(e)

            # 3. General Type action
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

            # Always capture screenshot
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
            "action": action
        }, indent=2))

if __name__ == "__main__":
    main()
