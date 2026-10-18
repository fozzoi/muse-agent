import sys
import json

raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"query": str(args)}
except Exception:
    args = {"query": raw}

query = (
    args.get("query")
    or args.get("q")
    or args.get("search")
    or args.get("input")
    or ""
).strip()

if not query:
    print("Error: 'query' parameter is required for web search (e.g. {'query': 'jailer 2 movie news'}).", file=sys.stderr)
    sys.exit(1)

try:
    from ddgs import DDGS
    with DDGS() as ddgs:
        items = list(ddgs.text(query, max_results=5))

    clean_results = []
    for item in items:
        clean_results.append({
            "title": item.get("title", ""),
            "url": item.get("href", ""),
            "snippet": item.get("body", ""),
        })

    print(json.dumps({
        "status": "success",
        "query": query,
        "count": len(clean_results),
        "results": clean_results
    }, indent=2))
except Exception as e:
    print(f"Error executing web search: {e}", file=sys.stderr)
    sys.exit(1)
