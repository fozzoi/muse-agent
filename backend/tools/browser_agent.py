#!/usr/bin/env python3
"""Hermes-Style Persistent Browser Harness for Muse Agent.
Connects to a persistent visible Chrome instance via CDP (port 9222),
injects DOM element indices [1], [2], [3] for 100% accurate clicking/typing,
and includes a dedicated YouTube auto-player.
"""
import os
import sys
import json
import time
import urllib.parse
import urllib.request
import subprocess
from playwright.sync_api import sync_playwright

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
PROFILE_DIR = os.path.expanduser("~/.config/muse-agent/browser-profile")
SCREENSHOTS_DIR = os.path.expanduser("~/Pictures/Screenshots")

# JavaScript injected into any webpage to index all interactive elements like Hermes/browser-use
DOM_INDEXER_JS = """
() => {
    // Remove old labels
    document.querySelectorAll('.muse-dom-label').forEach(el => el.remove());
    window.__muse_elements = {};
    let idx = 1;

    const selectors = 'a, button, input, textarea, select, [role="button"], [role="link"], ytd-video-renderer a#video-title, h3';
    const nodes = document.querySelectorAll(selectors);
    const items = [];

    nodes.forEach(el => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        if (rect.width < 10 || rect.height < 10 || style.visibility === 'hidden' || style.display === 'none') return;
        if (rect.top < 0 || rect.left < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) return;

        const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || el.value || '').trim().replace(/\\s+/g, ' ');
        const tag = el.tagName.toLowerCase();
        if (!text && tag !== 'input' && tag !== 'textarea') return;

        const currentIdx = idx++;
        el.setAttribute('data-muse-id', String(currentIdx));

        // Draw subtle visual badge on screen for vision grounding
        const badge = document.createElement('div');
        badge.className = 'muse-dom-label';
        badge.textContent = currentIdx;
        badge.style.cssText = `position:fixed;top:${Math.max(0, rect.top)}px;left:${Math.max(0, rect.left)}px;background:#ef4444;color:#fff;font-size:11px;font-weight:bold;padding:1px 4px;border-radius:3px;z-index:2147483647;pointer-events:none;`;
        document.body.appendChild(badge);

        items.push({
            id: currentIdx,
            tag: tag,
            type: el.getAttribute('type') || '',
            text: text.slice(0, 90),
            href: (el.getAttribute('href') || '').slice(0, 100)
        });
    });
    return items.slice(0, 60);
}
"""

def ensure_chrome_running():
    """Ensures a visible Chrome window is running with CDP port 9222 open."""
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1.5)
        return True
    except Exception:
        pass

    os.makedirs(PROFILE_DIR, exist_ok=True)
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = os.path.join(PROFILE_DIR, lock)
        if os.path.islink(p) or os.path.exists(p):
            try:
                os.unlink(p)
            except Exception:
                pass

    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")

    chrome_bins = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
    chrome_cmd = next((b for b in chrome_bins if subprocess.run(["which", b], capture_output=True).returncode == 0), "google-chrome")

    subprocess.Popen(
        [
            chrome_cmd,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required",
            "about:blank",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    for _ in range(20):
        time.sleep(0.4)
        try:
            urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1.0)
            return True
        except Exception:
            continue
    return False

def main():
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(args, dict):
            args = {"action": "goto", "url": str(raw)}
    except Exception:
        args = {"action": "goto", "url": str(raw)}

    action = str(args.get("action", "inspect")).lower().strip()
    url = str(args.get("url") or "").strip()
    query = str(args.get("query") or args.get("song") or args.get("search") or "").strip()
    element_id = args.get("id") or args.get("index") or args.get("element_id")
    text_to_type = str(args.get("text") or "").strip()

    if not ensure_chrome_running():
        print(json.dumps({"status": "error", "error": "Failed to launch or connect to Chrome on CDP port 9222."}))
        sys.exit(1)

    shot_path = os.path.join(SCREENSHOTS_DIR, f"browser_{int(time.time())}.png")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[-1] if context.pages else context.new_page()
        page.bring_to_front()
        page.set_default_timeout(20000)

        # 1. Dedicated 1-Step YouTube Search & Play
        if action in ("play_youtube", "youtube_play", "youtube"):
            search_q = query or text_to_type or url
            if not search_q:
                print(json.dumps({"status": "error", "error": "Missing 'query' for YouTube playback."}))
                sys.exit(1)
            yt_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(search_q)}"
            page.goto(yt_url, wait_until="domcontentloaded")
            # Wait for video titles to render in DOM
            page.wait_for_selector("ytd-video-renderer a#video-title", timeout=15000)
            time.sleep(1.0)
            first_video = page.locator("ytd-video-renderer a#video-title").first
            video_title = first_video.inner_text().strip()
            first_video.click()
            time.sleep(2.5)
            # Ensure video plays if paused
            try:
                page.evaluate("() => { const v = document.querySelector('video'); if (v && v.paused) v.play(); }")
            except Exception:
                pass
            page.screenshot(path=shot_path)
            print(json.dumps({
                "status": "success",
                "action": "play_youtube",
                "playing_title": video_title,
                "current_url": page.url,
                "file_path": shot_path,
            }, indent=2))
            return

        # 2. Navigate to URL
        if action in ("goto", "navigate", "open"):
            if not url:
                print(json.dumps({"status": "error", "error": "No URL provided. Pass 'url' in args."}))
                sys.exit(1)
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(1.5)

        # 3. Click by Element ID [index] or Text Match
        elif action == "click":
            if element_id is not None:
                loc = page.locator(f'[data-muse-id="{element_id}"]').first
                loc.scroll_into_view_if_needed()
                loc.click(timeout=8000)
            elif text_to_type or query:
                target_txt = text_to_type or query
                page.get_by_text(target_txt, exact=False).first.click(timeout=8000)
            time.sleep(1.5)

        # 4. Type into Element ID [index] or active search/input box
        elif action in ("type", "fill", "search"):
            if element_id is not None:
                loc = page.locator(f'[data-muse-id="{element_id}"]').first
                loc.click()
                loc.fill(text_to_type or query)
            else:
                loc = page.locator('input[type="search"], input[name="search_query"], input[name="q"], input[type="text"], textarea').first
                loc.click()
                loc.fill(text_to_type or query)
            if args.get("submit", True):
                page.keyboard.press("Enter")
            time.sleep(2.0)

        # 5. Scroll
        elif action == "scroll":
            direction = str(args.get("direction", "down")).lower()
            delta = 600 if direction == "down" else -600
            page.mouse.wheel(0, delta)
            time.sleep(1.0)

        # Index all visible interactive elements on the current page
        interactive_elements = page.evaluate(DOM_INDEXER_JS)
        page.screenshot(path=shot_path)

        print(json.dumps({
            "status": "success",
            "action": action,
            "title": page.title(),
            "current_url": page.url,
            "interactive_elements": interactive_elements,
            "file_path": shot_path,
        }, indent=2))

if __name__ == "__main__":
    main()
