import sys, json, os, subprocess

def is_gnome():
    # Check XDG_CURRENT_DESKTOP environment variable
    desktop_env = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    if 'gnome' in desktop_env:
        return True

    # Check if gnome-shell is running
    try:
        result = subprocess.run(['gnome-shell', '--version'],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              check=True)
        if result.returncode == 0:
            return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return False

# Parse input (though not used in this script)
raw_input = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""

# Determine if using GNOME
using_gnome = is_gnome()

# Print JSON output
print(json.dumps({'using_gnome': using_gnome}))