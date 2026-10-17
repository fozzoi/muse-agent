import platform
import os
import json
import sys

def main():
    # Get OS name
    os_name = platform.system()

    # Get Python version
    python_version = platform.python_version()

    # Get CPU count
    cpu_count = os.cpu_count()

    # Create a dictionary to hold the information
    info = {
        "os": os_name,
        "python_version": python_version,
        "cpu_count": cpu_count
    }

    # Print the dictionary as a JSON string
    print(json.dumps(info))

if __name__ == "__main__":
    main()