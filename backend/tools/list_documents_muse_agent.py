import sys, json, os

def list_directory_entries():
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

    if not target:
        target_path = os.path.expanduser("~/Documents/muse-agent")
    else:
        expanded = os.path.expanduser(target)
        if os.path.isabs(expanded) and os.path.exists(expanded):
            target_path = expanded
        else:
            home_candidate = os.path.join(os.path.expanduser("~"), target)
            doc_candidate = os.path.join(os.path.expanduser("~/Documents"), target)
            if os.path.exists(home_candidate):
                target_path = home_candidate
            elif os.path.exists(doc_candidate):
                target_path = doc_candidate
            else:
                target_path = home_candidate

    try:
        entries = os.listdir(str(target_path))
        print(json.dumps({"entries": entries}))
    except Exception:
        print(json.dumps({"entries": []}))

if __name__ == "__main__":
    list_directory_entries()