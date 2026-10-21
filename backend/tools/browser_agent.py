#!/usr/bin/env python3
"""Autonomous Browser Agent for Muse Agent.
Navigates the web, extracts page content, captures screenshots, clicks/types,
and performs persistent headless sessions or interactive desktop GUI logins.
"""
import os
import sys
import json
import time
import subprocess
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

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

    # 1. Interactive Desktop GUI Login Window
    if action in ("open_window", "open_headed", "gui", "desktop_window", "setup_login"):
        target_url = url or "https://www.instagram.com/accounts/login/"
        if not target_url.startswith(("http://", "https://")):
            target_url = f"https://{target_url}"

        print(json.dumps({
            "status": "waiting",
            "message": f"Opening browser window on desktop for {target_url}. Please complete login on screen."
        }))

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=False,
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target_url)

            # Wait up to 3 minutes for user to log in and navigate away from login/accounts
            try:
                page.wait_for_url(lambda u: "login" not in u and "accounts" not in u, timeout=180000)
                time.sleep(2.0)
                final_url = page.url
                context.close()
                print(json.dumps({
                    "status": "success",
                    "action": "setup_login",
                    "url": final_url,
                    "message": "Login successful! Session cookies saved permanently."
                }, indent=2))
            except Exception as e:
                context.close()
                print(json.dumps({"error": f"Headed login session closed or timed out: {e}"}))
        return

    # 2. Headless Mode (Default)
    if (username and password) or action in ("login", "headless_login"):
        action = "login"
        if not url:
            url = "https://www.instagram.com/accounts/login/"

    if not url:
        print(json.dumps({
            "status": "error",
            "error": "No URL provided for browser navigation."
        }))
        return

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
                headless=True,
                viewport={"width": 1280, "height": 800},
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )

            # Reuse existing page instead of creating a second blank tab
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(30000)

            # Stealth: Strip navigator.webdriver flag
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

            # Navigate
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(2.0)

            # Headless Login Flow
            if action == "login" and username and password:
                user_selectors = [
                    'input[name="username"]',
                    'input[name="email"]',
                    'input[type="text"]',
                    'input[type="email"]',
                ]
                pass_selectors = [
                    'input[name="password"]',
                    'input[name="pass"]',
                    'input[type="password"]',
                ]
                submit_selectors = [
                    'button[type="submit"]',
                    'input[type="submit"]',
                    'form button',
                    '[role="button"]:has-text("Log In")',
                    '[role="button"]:has-text("Sign in")',
                ]

                # Dismiss cookie popups if present
                for cookie_btn in ('button:has-text("Allow all cookies")', 'button:has-text("Accept")', 'button:has-text("Decline optional cookies")'):
                    try:
                        btn = page.locator(cookie_btn).first
                        if btn.is_visible():
                            btn.click(timeout=1500)
                            time.sleep(1.0)
                            break
                    except Exception:
                        pass

                # Fill credentials
                filled_user = False
                for sel in user_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible():
                            loc.fill(username)
                            filled_user = True
                            break
                    except Exception:
                        continue

                filled_pass = False
                for sel in pass_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible():
                            loc.fill(password)
                            filled_pass = True
                            break
                    except Exception:
                        continue

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

                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        time.sleep(3.0)

                result["login_attempted"] = True
                result["username_filled"] = filled_user
                result["password_filled"] = filled_pass
                result["submitted"] = submitted

                page_text_lower = page.inner_text("body").lower()
                if any(x in page_text_lower for x in ("security code", "two-factor", "verification code", "enter the 6-digit")):
                    result["login_status"] = "2FA_REQUIRED"
                    result["notice"] = "Instagram requires a 2FA/SMS code. Run with action='open_window' to solve on desktop."
                elif any(x in page_text_lower for x in ("incorrect password", "the password that you've entered is incorrect", "sorry, your password was incorrect")):
                    result["login_status"] = "INVALID_PASSWORD"
                    result["notice"] = "Instagram reported the password was incorrect."
                elif "/login" not in page.url and any(x in page_text_lower for x in ("home", "search", "explore", "reels", "messages", "profile")):
                    result["login_status"] = "SUCCESS"
                    result["notice"] = "Successfully logged in headlessly! Session cookies saved permanently."
                else:
                    result["login_status"] = "PENDING_OR_CHALLENGED"
                    result["notice"] = f"Submitted. Current URL: {page.url}"

            # General Click Action
            click_sel = args.get("click_selector") or args.get("selector")
            if action in ("click", "interact") and click_sel:
                try:
                    page.click(click_sel, timeout=5000)
                    time.sleep(1.0)
                    result["clicked_selector"] = click_sel
                except Exception as e:
                    result["click_warning"] = str(e)

            # General Type Action
            type_text = args.get("text") or args.get("type_text")
            if action in ("type", "fill") and click_sel and type_text:
                try:
                    page.fill(click_sel, type_text, timeout=5000)
                    if args.get("submit", False):
                        page.keyboard.press("Enter")
                        time.sleep(2.0)
                    result["typed_into"] = click_sel
                except Exception as e:
                    result["type_warning"] = str(e)

            # Capture Content & Screenshot
            title = page.title()
            current_url = page.url
            readable_text = clean_html(page.content())
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