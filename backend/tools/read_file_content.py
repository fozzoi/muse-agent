import sys, json, os

raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ""
target = ""

if raw:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            target = (
                data.get("path")
                or data.get("directory")
                or data.get("folder")
                or data.get("target")
                or ""
            )
        elif isinstance(data, str):
            target = data
    except Exception:
        target = str(raw).strip()

# Path normalization & resolution:
if not target:
    target_path = os.path.expanduser("~/Documents")
else:
    expanded = os.path.expanduser(target)
    if os.path.isabs(expanded) and os.path.exists(expanded):
        target_path = expanded
    else:
        # Check relative to ~ and ~/Documents
        home_candidate = os.path.join(os.path.expanduser("~"), target)
        doc_candidate = os.path.join(os.path.expanduser("~/Documents"), target)
        if os.path.exists(home_candidate):
            target_path = home_candidate
        elif os.path.exists(doc_candidate):
            target_path = doc_candidate
        else:
            target_path = home_candidate  # Pass resolved candidate to trigger accurate error if missing

try:
    with open(str(target_path), 'r') as file:
        content = file.read()
    print(content)
except Exception as e:
    print(f"Error reading file: {e}")