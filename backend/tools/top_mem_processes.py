import sys
import json
import subprocess

# ---- Argument parsing (template) ----
raw = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "{}"
try:
    args = json.loads(raw)
    if not isinstance(args, dict):
        args = {"input": str(args)}
except Exception:
    args = {"input": raw}

# ---- Core logic ----
# Command to list top 5 memory‑consuming processes
cmd = ["ps", "-eo", "pid,comm,%mem", "--sort=-%mem"]
# Run the command and capture its output
output_bytes = subprocess.check_output(cmd, text=True)

# Split into lines, keep header + top 5 entries
lines = output_bytes.strip().splitlines()
# Ensure we have at least header + 5 rows
result_lines = lines[:6]  # header + 5 processes

# Print the result exactly as required
print("\n".join(result_lines))