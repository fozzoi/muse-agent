import sys, json, os
import asyncio
import subprocess
from playwright.async_api import async_playwright
from playwright._impl._errors import Error as PlaywrightError

# Parse sys.argv[1]
raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}

# Path resolution function (as per rules)
def resolve_path(p: str) -> str:
    if not p:
        return os.path.expanduser("~")
    p = os.path.expanduser(str(p).strip())
    if os.path.isabs(p) and os.path.exists(p):
        return p
    for base in [os.path.expanduser("~"), os.path.expanduser("~/Documents"), os.path.expanduser("~/Documents/muse-agent")]:
        cand = os.path.join(base, p.lstrip("/\\"))
        if os.path.exists(cand):
            return cand
    return os.path.join(os.path.expanduser("~"), p.lstrip("/\\"))

async def main():
    """
    Navigates to a given URL using Playwright, extracts the computed CSS 'color'
    property of the <body> element, and if present, the first <h1> element.
    It prints these colors as a JSON object to stdout.
    """
    url = args.get("url")

    if not url:
        print("Error: 'url' argument is required.", file=sys.stderr)
        sys.exit(1)

    result = {
        "body_color": None,
        "h1_color": None
    }

    browser = None
    try:
        async with async_playwright() as p:
            try:
                # Attempt to launch the browser. This is where the original error occurred.
                browser = await p.chromium.launch() # Headless browser by default
            except PlaywrightError as e:
                # Catch the specific Playwright error indicating missing browser executables
                print(f"Playwright browser launch failed: {e}", file=sys.stderr)
                print("\nIt appears Playwright's browser executables are not installed.", file=sys.stderr)
                print("Attempting to install them now using 'python -m playwright install'...", file=sys.stderr)
                
                try:
                    # Attempt to run playwright install using the current Python interpreter's module
                    install_command = [sys.executable, "-m", "playwright", "install"]
                    
                    # Run the installation command, capturing output and checking for errors
                    install_process = subprocess.run(
                        install_command, 
                        check=True, 
                        capture_output=True, 
                        text=True,
                        env=os.environ # Pass current environment to ensure correct PATH, etc.
                    )
                    print("Playwright browsers installed successfully.", file=sys.stderr)
                    if install_process.stdout:
                        print(f"Installation output:\n{install_process.stdout}", file=sys.stderr)
                    if install_process.stderr:
                        print(f"Installation errors/warnings:\n{install_process.stderr}", file=sys.stderr)
                    
                    # After successful installation, retry launching the browser
                    browser = await p.chromium.launch()
                    print("Browser launched successfully after installation.", file=sys.stderr)

                except subprocess.CalledProcessError as install_e:
                    print(f"Failed to install Playwright browsers: Command '{' '.join(install_e.cmd)}' returned non-zero exit status {install_e.returncode}.", file=sys.stderr)
                    print(f"Stdout: {install_e.stdout}", file=sys.stderr)
                    print(f"Stderr: {install_e.stderr}", file=sys.stderr)
                    print("\nPlease run 'playwright install' manually in your terminal to fix this.", file=sys.stderr)
                    sys.exit(1)
                except FileNotFoundError:
                    print(f"Error: 'python' or 'playwright' command not found. Ensure Python and Playwright are installed and in your PATH.", file=sys.stderr)
                    print("\nPlease run 'playwright install' manually in your terminal to fix this.", file=sys.stderr)
                    sys.exit(1)
                except Exception as retry_e:
                    print(f"An unexpected error occurred after attempting Playwright installation: {retry_e}", file=sys.stderr)
                    sys.exit(1)

            page = await browser.new_page()

            # Navigate to the URL and wait for the DOM to be loaded
            await page.goto(url, wait_until="domcontentloaded", timeout=30000) # 30-second timeout

            # Get the computed color of the <body> element
            body_element = await page.query_selector("body")
            if body_element:
                body_color = await body_element.evaluate("element => window.getComputedStyle(element).color")
                result["body_color"] = body_color

            # Get the computed color of the first <h1> element, if it exists
            h1_element = await page.query_selector("h1")
            if h1_element:
                h1_color = await h1_element.evaluate("element => window.getComputedStyle(element).color")
                result["h1_color"] = h1_color

    except Exception as e:
        # Catch any other general exceptions that might occur during page operations
        print(f"An unexpected error occurred while processing URL '{url}': {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Ensure the browser is closed if it was successfully launched
        if browser:
            await browser.close()
    
    # Print the result as a JSON string to stdout
    print(json.dumps(result))

if __name__ == "__main__":
    asyncio.run(main())