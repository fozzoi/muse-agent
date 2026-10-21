"""Autonomous Goal Mode Engine for Muse Agent on Ubuntu Linux.
Executes long-running, multi-step objectives autonomously until completion,
performing sub-task planning, tool execution, visual verification, self-healing,
and progress reporting.
"""

import os
import re
import json
import time
import base64
import asyncio
from typing import Callable, Coroutine, Any

from core.router import generate
from core.sandbox import run_tool, create_and_run_tool
from core.database import load_local_manifest
from core.user_profile import get_user_profile_context
from core.journal import record_lesson
from core.memory import extract_image_path

GOAL_PLANNER_SYSTEM_PROMPT = """You are the Lead Autonomous Mission Commander for Muse Agent on Ubuntu Linux desktop.
The user has assigned you a high-level GOAL. Your responsibility is to autonomously and iteratively achieve this goal completely, adapting to errors, using tools, and verifying outcomes with visual and diagnostic evidence.

CRITICAL OPERATIONAL RULES:
1. SCREEN & DISPLAY:
   - Screen resolution: 1920x1080 pixels (X: 0 to 1920, Y: 0 to 1080).
   - Ubuntu GNOME top bar is at Y < 40; bottom application dock is at Y > 980.
   - All interactive application windows, browsers, and media controls reside between X: 50 to 1870 and Y: 80 to 950.
   - NEVER click Y > 980 or Y < 40 for application/media interactions.

2. GROUND TRUTH VERIFICATION & NO BLUFFING:
   - You MUST NOT mark a goal as COMPLETE unless tangible evidence proves the objective was achieved (e.g. screenshot verifies video is playing, command exit code is 0, file exists with valid content).
   - If a tool fails, reflect on the error output, diagnose the cause, and try an alternative approach.

3. AUTONOMOUS PROGRESS:
   - Break complex work into clear milestones.
   - Execute one high-leverage action per turn.
   - When all success criteria are visually or diagnostically confirmed, emit 'COMPLETE'.

Return raw JSON ONLY matching ONE of these structures:

Structure A: NEXT ACTION (To advance the goal):
{
  "action": "USE_TOOL",
  "tool_name": "exact_name_from_manifest",
  "args": {"param": "value"},
  "subgoal_index": 0,
  "thought": "Clear technical reasoning for this step and what it accomplishes",
  "update_subgoals": [{"index": 0, "status": "completed|in_progress|pending"}]
}

Structure B: COMPLETE (Only when goal is 100% verified):
{
  "action": "COMPLETE",
  "summary": "Detailed summary of everything accomplished and verified",
  "verification_evidence": "Concrete visual or diagnostic proof confirming completion"
}

Structure C: CREATE TOOL (Last resort if no existing tool can perform the task):
{
  "action": "MAKE_TOOL",
  "tool_name": "short_snake_case_name",
  "description": "Specification of what tool to synthesize",
  "args": {}
}
"""

def _parse_json_robust(text: str) -> dict:
    """Extracts and parses JSON object from model response."""
    text = re.sub(r"```(?:json)?\s*(.*?)```", r"\1", text, flags=re.DOTALL).strip()
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    return {}

async def plan_goal_mission(objective: str) -> dict:
    """Analyzes user objective and constructs an autonomous mission plan with subgoals and criteria."""
    user_profile = get_user_profile_context()
    manifest = load_local_manifest()
    tool_names = list(manifest.keys())

    prompt = (
        f"{user_profile}\n\n"
        f"Available Local Tools: {', '.join(tool_names)}\n\n"
        f"USER OBJECTIVE: \"{objective}\"\n\n"
        f"Break down this objective into a realistic autonomous mission plan.\n"
        f"Return raw JSON only with:\n"
        f"- 'title': Short descriptive mission title\n"
        f"- 'subgoals': Array of 2 to 5 sequential milestone strings\n"
        f"- 'success_criteria': Exact measurable condition proving 100% completion (e.g. 'YouTube video player active with progress bar running', 'File ~/Desktop/report.md created with >500 words')\n"
        f"- 'estimated_iterations': Estimated number of tool steps (3 to 15)\n\n"
        f"Example format:\n"
        f'{{"title": "Play Song on YouTube", "subgoals": ["Focus Google Chrome", "Navigate to YouTube search", "Click video and verify playback"], "success_criteria": "Video playback active on screen", "estimated_iterations": 4}}'
    )

    try:
        res = await generate("heavy", prompt, system="You are an autonomous AI planner. Return raw JSON only.")
        plan = _parse_json_robust(res.get("text", ""))
    except Exception as e:
        print(f"[GoalManager] Planning fallback due to: {e}", flush=True)
        plan = {}

    title = plan.get("title") or objective[:40]
    subgoals_raw = plan.get("subgoals") or [
        f"Inspect active environment and prerequisites for '{objective[:30]}'",
        f"Execute core operations for '{objective[:30]}'",
        f"Verify final outcome and confirm completion",
    ]
    subgoals = [{"title": str(sg), "status": "pending"} for sg in subgoals_raw]
    if subgoals:
        subgoals[0]["status"] = "in_progress"

    success_criteria = plan.get("success_criteria") or f"Objective '{objective}' successfully executed and verified."
    max_iterations = min(20, max(5, int(plan.get("estimated_iterations", 10)) + 3))

    goal_id = f"goal_{int(time.time())}"
    return {
        "id": goal_id,
        "objective": objective,
        "title": title,
        "subgoals": subgoals,
        "success_criteria": success_criteria,
        "max_iterations": max_iterations,
        "current_iteration": 0,
        "tool_trace": [],
        "combined_raw": [],
        "images_collected": [],
        "status": "in_progress",
        "start_time": time.time(),
        "last_update": time.time(),
        "summary": "",
        "verification_evidence": "",
    }

def format_goal_progress(goal: dict) -> str:
    """Formats a live, visually appealing Telegram Markdown progress card."""
    elapsed = int(time.time() - goal.get("start_time", time.time()))
    iter_curr = goal.get("current_iteration", 0)
    iter_max = goal.get("max_iterations", 15)

    # Progress bar
    total_sg = len(goal.get("subgoals", []))
    completed_sg = sum(1 for sg in goal.get("subgoals", []) if sg.get("status") == "completed")
    pct = int((completed_sg / max(1, total_sg)) * 100) if total_sg else 0
    filled = int(pct / 10)
    bar = "▓" * filled + "░" * (10 - filled)

    lines = [
        f"🎯 *Goal in Progress:* \"_{goal.get('title', 'Mission')}_\"",
        f"📊 *Progress:* `[{bar}]` {pct}% ({completed_sg}/{total_sg} subgoals)",
        f"⏱️ *Step:* {iter_curr}/{iter_max} • *Elapsed:* {elapsed}s",
        "",
        "📋 *Mission Checklist:*",
    ]

    for i, sg in enumerate(goal.get("subgoals", [])):
        st = sg.get("status", "pending")
        if st == "completed":
            icon = "✅"
        elif st == "in_progress":
            icon = "⏳"
        elif st == "failed":
            icon = "❌"
        else:
            icon = "◻️"
        lines.append(f"{icon} {i+1}. {sg.get('title')}")

    lines.append("")
    lines.append(f"🔍 *Target:* `{goal.get('success_criteria')}`")
    lines.append("👉 Send `/stop` to abort this mission anytime.")

    return "\n".join(lines)

async def run_goal_mission(
    goal: dict,
    status_updater: Callable[[str], Coroutine[Any, Any, None]],
    cancel_event: asyncio.Event,
) -> dict:
    """Executes the autonomous goal loop until completion, failure, or cancellation."""
    manifest = load_local_manifest()
    user_profile = get_user_profile_context()
    max_iterations = goal.get("max_iterations", 15)

    while goal["current_iteration"] < max_iterations:
        if cancel_event.is_set():
            goal["status"] = "stopped"
            return goal

        goal["current_iteration"] += 1
        iter_num = goal["current_iteration"]
        goal["last_update"] = time.time()

        # Update Telegram status with progress
        progress_msg = format_goal_progress(goal)
        try:
            await status_updater(progress_msg)
        except Exception:
            pass

        # Prepare context for the mission planner
        subgoals_text = "\n".join([
            f"- Subgoal {i+1} [{sg.get('status', 'pending').upper()}]: {sg.get('title')}"
            for i, sg in enumerate(goal["subgoals"])
        ])

        if not goal["tool_trace"]:
            history_summary = "(No actions executed yet. This is Step 1.)"
        else:
            history_summary = "\n\n".join([
                f"[Step {i+1} - {st['tool']}]: Output:\n{st['output'] if len(st['output']) <= 2500 else (st['output'][:1200] + '...[truncated]...' + st['output'][-1000:])}"
                for i, st in enumerate(goal["tool_trace"])
            ])

        planner_prompt = (
            f"{user_profile}\n\n"
            f"GOAL OBJECTIVE: \"{goal['objective']}\"\n"
            f"SUCCESS CRITERIA: \"{goal['success_criteria']}\"\n"
            f"CURRENT PROGRESS (Step {iter_num} of {max_iterations}):\n"
            f"{subgoals_text}\n\n"
            f"AVAILABLE TOOLS:\n{json.dumps(manifest)}\n\n"
            f"ACTION HISTORY & DIAGNOSTICS SO FAR:\n"
            f"{history_summary}\n\n"
            f"Evaluate current status against the success criteria:\n"
            f"- If ALL success criteria are confirmed and proven by evidence: emit 'COMPLETE'.\n"
            f"- Otherwise: determine the exact next action to advance the active subgoal."
        )

        try:
            route_res = await generate("heavy", planner_prompt, system=GOAL_PLANNER_SYSTEM_PROMPT)
            if cancel_event.is_set():
                goal["status"] = "stopped"
                return goal
            decision = _parse_json_robust(route_res.get("text", ""))
        except Exception as e:
            print(f"[GoalManager] Planning error at step {iter_num}: {e}", flush=True)
            decision = {"action": "USE_TOOL", "tool_name": "take_screenshot", "args": {}}

        action = decision.get("action", "USE_TOOL")
        thought = decision.get("thought", "")

        # Update any subgoals modified by model
        for usg in decision.get("update_subgoals", []):
            idx = usg.get("index")
            new_st = usg.get("status")
            if idx is not None and 0 <= idx < len(goal["subgoals"]) and new_st:
                goal["subgoals"][idx]["status"] = new_st

        if action == "COMPLETE":
            goal["status"] = "completed"
            goal["summary"] = decision.get("summary") or f"Successfully completed objective: {goal['objective']}"
            goal["verification_evidence"] = decision.get("verification_evidence") or "All milestones executed and verified."
            print(f"[GoalManager] 🎉 Goal completed at step {iter_num}: {goal['summary'][:100]}", flush=True)

            # Record lesson in experience journal
            try:
                record_lesson(
                    title=f"Autonomous Goal: {goal['title']}",
                    problem=f"Objective: {goal['objective']}",
                    solution=goal["summary"],
                    tags=["goal", "autonomous", "mission"]
                )
            except Exception:
                pass
            return goal

        if action == "MAKE_TOOL":
            t_name = decision.get("tool_name", f"tool_step_{iter_num}")
            t_desc = decision.get("description", goal["objective"])
            tool_args = decision.get("args", {})
            try:
                build_res = await create_and_run_tool(t_name, t_desc, tool_args)
                goal["tool_trace"].append({"tool": t_name, "args": tool_args, "output": build_res.get("output", "")})
                manifest = load_local_manifest()
            except Exception as be:
                goal["tool_trace"].append({"tool": t_name, "args": tool_args, "output": f"Tool build error: {be}"})
            continue

        if action == "USE_TOOL":
            t_name = decision.get("tool_name", "")
            tool_args = decision.get("args", {})
            if not t_name:
                t_name = "take_screenshot"
                tool_args = {}

            print(f"[GoalManager] Step {iter_num}: Running '{t_name}' with {tool_args} (Thought: {thought[:60]}...)", flush=True)

            loop = asyncio.get_running_loop()
            try:
                success, output = await loop.run_in_executor(None, run_tool, t_name, tool_args)
            except Exception as te:
                success, output = False, f"Execution exception: {te}"

            if cancel_event.is_set():
                goal["status"] = "stopped"
                return goal

            # If screenshot was taken, analyze visually with AI (1920x1080 resolution)
            if t_name == "take_screenshot" and success:
                img_path = extract_image_path(output)
                if img_path and os.path.isfile(img_path):
                    if img_path not in goal["images_collected"]:
                        goal["images_collected"].append(img_path)
                    try:
                        with open(img_path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("utf-8")
                        vis_prompt = (
                            f"You are the visual cortex for Muse Agent on Ubuntu Linux desktop.\n"
                            f"CURRENT GOAL: '{goal['objective']}'\n"
                            f"SUCCESS CRITERIA: '{goal['success_criteria']}'\n\n"
                            f"SCREEN METRICS:\n"
                            f"- Resolution: 1920x1080 pixels (X: 0 to 1920, Y: 0 to 1080).\n"
                            f"- Safe Canvas: Y: 80 to 950 (avoid top panel Y < 40 and bottom dock Y > 980).\n\n"
                            f"Analyze this active desktop screenshot:\n"
                            f"1. Is the goal objective currently satisfied on screen? Explain what is visible.\n"
                            f"2. What active window is focused?\n"
                            f"3. What exact coordinates (X, Y) or next step is required if not finished?"
                        )
                        vis_res = await generate("vision", vis_prompt, image_data=b64, mime_type="image/png")
                        vis_analysis = vis_res.get("text", "")
                        output = json.dumps({
                            "status": "success",
                            "file": img_path,
                            "ai_vision_analysis": vis_analysis
                        })
                    except Exception as ve:
                        print(f"[GoalManager] Vision analysis error: {ve}", flush=True)

            goal["tool_trace"].append({"tool": t_name, "args": tool_args, "output": output})
            goal["combined_raw"].append(f"[{t_name}]:\n{output}")
            continue

    # Reached maximum iterations
    goal["status"] = "max_iterations_reached"
    goal["summary"] = f"Mission reached the maximum budget of {max_iterations} steps. Final state requires user review."
    return goal
