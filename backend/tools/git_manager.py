#!/usr/bin/env python3
"""Git Manager Tool for Muse Agent (Inspect status, logs, branches, and diffs of local repositories)."""
import os
import sys
import json
import subprocess

def resolve_path(p: str) -> str:
    if not p:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    p = os.path.expanduser(str(p).strip())
    if os.path.isdir(p):
        return os.path.abspath(p)
    # Check common roots
    home = os.path.expanduser("~")
    for base in [home, os.path.join(home, "Documents"), os.path.join(home, "Documents", "muse-agent")]:
        cand = os.path.join(base, p.lstrip("/"))
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    return p

def run_git(args: list[str], cwd: str) -> tuple[int, str]:
    try:
        res = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=10)
        return res.returncode, (res.stdout or res.stderr).strip()
    except Exception as e:
        return 1, str(e)

def parse_args():
    raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"action": str(raw)}
    except Exception:
        return {"action": str(raw)}

def main():
    args = parse_args()
    path_arg = args.get("path") or args.get("repo_path") or args.get("directory") or ""
    repo_dir = resolve_path(path_arg)
    action = str(args.get("action", "status")).lower().strip()

    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        print(json.dumps({
            "error": f"Path '{repo_dir}' is not a valid git repository (missing .git directory).",
            "searched_path": repo_dir
        }, indent=2))
        return

    result = {
        "repo_path": repo_dir
    }

    # Branch
    _, branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_dir)
    result["current_branch"] = branch

    if "log" in action:
        n = str(args.get("limit", 5))
        _, logs = run_git(["log", f"-n{n}", "--pretty=format:%h - %an (%cr): %s"], repo_dir)
        result["recent_commits"] = logs.splitlines() if logs else []

    elif "diff" in action:
        _, diff_stat = run_git(["diff", "--stat"], repo_dir)
        result["unstaged_diff_stat"] = diff_stat or "No unstaged changes"

    elif "branches" in action or "branch" in action:
        _, branches = run_git(["branch", "-a"], repo_dir)
        result["branches"] = [b.strip() for b in branches.splitlines()]

    else:
        # Default: status summary + last commit
        _, status_s = run_git(["status", "-s"], repo_dir)
        _, last_commit = run_git(["log", "-1", "--pretty=format:%h - %an (%cr): %s"], repo_dir)
        result["status_porcelain"] = status_s.splitlines() if status_s else ["Working tree clean"]
        result["last_commit"] = last_commit

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
