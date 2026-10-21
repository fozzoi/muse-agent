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

# ---------------------------------------------------------------------------
# How many *real* tool actions (clicks, types, key presses, commands — NOT
# plain screenshots) must be executed before the planner is even allowed to
# emit COMPLETE.  This prevents the model from doing nothing and bluffing.
# ---------------------------------------------------------------------------
MIN_REAL_ACTIONS_BEFORE_COMPLETE = 3

GOAL_PLANNER_SYSTEM_PROMPT = """You are the Lead Autonomous Mission Commander for Muse Agent on Ubuntu Linux desktop.
The user has assigned you a high-level GOAL. Your responsibility is to autonomously and iteratively achieve this goal completely, adapting to errors, using tools, and verifying outcomes with visual and diagnostic evidence.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL OPERATIONAL RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. SCREEN & DISPLAY:
   - Screen resolution: 1920x1080 pixels (X: 0‑1920, Y: 0‑1080).
   - Ubuntu GNOME top bar is at Y < 40; bottom application dock is at Y > 980.
   - All interactive application windows, browsers, and media controls reside
     between X: 50‑1870 and Y: 80‑950.
   - NEVER click Y > 980 or Y < 40 for application/media interactions.

2. ZERO-BLUFF ANTI-HALLUCINATION POLICY (MOST CRITICAL):
   - You MUST NOT mark a goal as COMPLETE unless you have *tangible, verifiable
     evidence* that the objective was achieved. Evidence means:
       • A screenshot AI-vision analysis that explicitly confirms the target
         state (e.g. "YouTube video is playing", "file exists", "application
         window is open showing …").
       • A command exit code of 0 with expected stdout content.
   - ❌ NEVER return COMPLETE on your first or second step.
   - ❌ NEVER return COMPLETE after only taking a screenshot.
   - ❌ NEVER return COMPLETE unless you have executed at least 3 real actions
     (click, type, key, sys_exec, focus — screenshot alone does NOT count).
   - ❌ NEVER return COMPLETE "optimistically" or by assuming something worked.
   - If a tool fails, REFLECT on the error output, diagnose the cause, and try
     an alternative approach — do NOT give up or bluff success.
   - A screenshot showing the desktop with NO change from a previous screenshot
     is NOT evidence of completion — it means your action didn't work.

3. AUTONOMOUS PROGRESS (HOW TO ADVANCE THE GOAL):
   - Break complex work into clear milestones via subgoals.
   - Execute one high-leverage action per turn.
   - ALWAYS alternate: perform an action → take screenshot to verify → next action.
   - When you're about to claim COMPLETE, your PREVIOUS action must have been
     take_screenshot and the vision analysis must confirm success criteria.

4. TYPICAL WORKFLOW for GUI goals:
   Step 1: take_screenshot → understand what's on screen.
   Step 2: desktop_navigator (focus the target app, or open it via sys_exec).
   Step 3: take_screenshot → verify the app is now focused/open.
   Step 4: desktop_navigator (click, type, interact).
   Step 5: take_screenshot → verify the interaction worked.
   … repeat until the goal is clearly achieved on screen.
   Final: COMPLETE — only after the last screenshot proves it.

Return raw JSON ONLY matching ONE of these structures:

Structure A: NEXT ACTION (advance the goal):
{
  "action": "USE_TOOL",
  "tool_name": "exact_name_from_manifest",
  "args": {"param": "value"},
  "subgoal_index": 0,
  "thought": "Clear technical reasoning for this step and what it accomplishes",
  "update_subgoals": [{"index": 0, "status": "completed|in_progress|pending"}]
}

Structure B: COMPLETE (ONLY when goal is 100% verified with evidence):
{
  "action": "COMPLETE",
  "summary": "Detailed summary of everything accomplished",
  "verification_evidence": "Exact quote from vision analysis or tool output proving success"
}
RULE: You may ONLY use Structure B if the current ACTION HISTORY shows at least
3 non-screenshot tool executions AND the most recent screenshot's ai_vision_analysis
confirms the success criteria. Otherwise you MUST use Structure A.

Structure C: CREATE TOOL (last resort if no existing tool can perform the task):
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


def _count_real_actions(tool_trace: list[dict]) -> int:
    """Count non-screenshot tool actions (clicks, types, exec, focus, etc.)."""
    screenshot_only = {"take_screenshot"}
    return sum(1 for t in tool_trace if t.get("tool") not in screenshot_only)


def _last_vision_confirms_criteria(tool_trace: list[dict], success_criteria: str) -> bool:
    """Check if the most recent screenshot's AI vision analysis contains evidence
    that matches the success criteria keywords."""
    # Find the last screenshot entry with vision analysis
    for entry in reversed(tool_trace):
        if entry.get("tool") == "take_screenshot":
            output = entry.get("output", "")
            try:
                data = json.loads(output)
                vision_text = data.get("ai_vision_analysis", "").lower()
            except (json.JSONDecodeError, AttributeError):
                vision_text = output.lower()

            if not vision_text:
                return False

            # Extract key words from success criteria (3+ chars, not common words)
            stopwords = {
                "the", "and", "for", "with", "that", "this", "from",
                "are", "was", "were", "has", "have", "had", "been",
                "will", "can", "may", "not", "all", "any", "but",
                "its", "our", "you", "your", "use", "via",
            }
            criteria_words = [
                w.lower() for w in re.findall(r"\w+", success_criteria)
                if len(w) >= 3 and w.lower() not in stopwords
            ]
            if not criteria_words:
                return True  # No meaningful keywords to check

            # Require at least 30% of criteria keywords found in vision text
            matches = sum(1 for w in criteria_words if w in vision_text)
            ratio = matches / len(criteria_words)
            return ratio >= 0.3

    return False


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
        f"- 'estimated_iterations': Estimated number of tool steps (5 to 15)\n\n"
        f"Example format:\n"
        f'{{"title": "Play Song on YouTube", "subgoals": ["Take screenshot to assess current desktop state", "Focus or launch Google Chrome", "Navigate to YouTube and search for the song", "Click the correct video result", "Verify video is playing on screen"], "success_criteria": "YouTube video player visible and actively playing the requested song", "estimated_iterations": 8}}'
    )

    try:
        res = await generate("heavy", prompt, system="You are an autonomous AI planner. Return raw JSON only.")
        plan = _parse_json_robust(res.get("text", ""))
    except Exception as e:
        print(f"[GoalManager] Planning fallback due to: {e}", flush=True)
        plan = {}

    title = plan.get("title") or objective[:40]
    subgoals_raw = plan.get("subgoals") or [
        f"Inspect current desktop state",
        f"Launch/focus required application for '{objective[:30]}'",
        f"Execute core operations for '{objective[:30]}'",
        f"Verify final outcome with screenshot confirmation",
    ]
    subgoals = [{"title": str(sg), "status": "pending"} for sg in subgoals_raw]
    if subgoals:
        subgoals[0]["status"] = "in_progress"

    success_criteria = plan.get("success_criteria") or f"Objective '{objective}' successfully executed and verified on screen."
    max_iterations = min(25, max(8, int(plan.get("estimated_iterations", 10)) + 5))

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
        "last_vision_analysis": "",
    }


def format_goal_progress(goal: dict) -> str:
    """Formats a live, visually appealing Telegram Markdown progress card."""
    elapsed = int(time.time() - goal.get("start_time", time.time()))
    iter_curr = goal.get("current_iteration", 0)
    iter_max = goal.get("max_iterations", 15)
    real_actions = _count_real_actions(goal.get("tool_trace", []))

    # Progress bar
    total_sg = len(goal.get("subgoals", []))
    completed_sg = sum(1 for sg in goal.get("subgoals", []) if sg.get("status") == "completed")
    pct = int((completed_sg / max(1, total_sg)) * 100) if total_sg else 0
    filled = int(pct / 10)
    bar = "▓" * filled + "░" * (10 - filled)

    lines = [
        f"🎯 *Goal in Progress:* \"_{goal.get('title', 'Mission')}_\"",
        f"📊 *Progress:* `[{bar}]` {pct}% ({completed_sg}/{total_sg} subgoals)",
        f"⏱️ *Step:* {iter_curr}/{iter_max} • *Actions:* {real_actions} • *Elapsed:* {elapsed}s",
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

    # Show last vision analysis snippet if available
    last_vis = goal.get("last_vision_analysis", "")
    if last_vis:
        snippet = last_vis[:120].replace("`", "'").replace("*", "")
        lines.append(f"👁️ *Last Screen:* _{snippet}..._")

    lines.append("👉 Send `/stop` to abort this mission anytime.")

    return "\n".join(lines)


async def run_goal_mission(
    goal: dict,
    status_updater: Callable[[str], Coroutine[Any, Any, None]],
    cancel_event: asyncio.Event,
) -> dict:
    """Executes the autonomous goal loop until completion, failure, or cancellation.

    Key anti-bluff safeguards:
    1. COMPLETE is rejected if fewer than MIN_REAL_ACTIONS_BEFORE_COMPLETE non-screenshot
       tool actions have been executed.
    2. COMPLETE is rejected unless the last screenshot vision analysis matches success criteria.
    3. If the model returns COMPLETE prematurely, it's overridden to take a screenshot
       or perform the next logical action instead.
    4. If the model returns an empty/unparseable response, a sensible fallback action
       is chosen based on current state.
    """
    manifest = load_local_manifest()
    user_profile = get_user_profile_context()
    max_iterations = goal.get("max_iterations", 15)

    # Count of consecutive screenshot-only iterations to detect loops
    consecutive_screenshots = 0
    MAX_CONSECUTIVE_SCREENSHOTS = 2

    while goal["current_iteration"] < max_iterations:
        if cancel_event.is_set():
            goal["status"] = "stopped"
            return goal

        goal["current_iteration"] += 1
        iter_num = goal["current_iteration"]
        goal["last_update"] = time.time()
        real_action_count = _count_real_actions(goal["tool_trace"])

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
            history_summary = "(No actions executed yet. This is Step 1. You MUST start by taking a screenshot to assess the current desktop state.)"
        else:
            history_lines = []
            for i, st in enumerate(goal["tool_trace"]):
                out = st["output"]
                if len(out) > 2500:
                    out = out[:1200] + "\n...[truncated]...\n" + out[-1000:]
                history_lines.append(f"[Step {i+1} - {st['tool']}]: Output:\n{out}")
            history_summary = "\n\n".join(history_lines)

        # Build enforcement context
        enforcement_lines = []
        if real_action_count < MIN_REAL_ACTIONS_BEFORE_COMPLETE:
            remaining = MIN_REAL_ACTIONS_BEFORE_COMPLETE - real_action_count
            enforcement_lines.append(
                f"⚠️ ENFORCEMENT: You have only performed {real_action_count} real actions "
                f"(screenshots don't count). You need at least {remaining} more real actions "
                f"(click, type, key, focus, sys_exec) before COMPLETE is allowed."
            )
        if iter_num <= 2:
            enforcement_lines.append(
                f"⚠️ ENFORCEMENT: This is only step {iter_num}. COMPLETE is NOT allowed on steps 1 or 2."
            )

        enforcement_block = "\n".join(enforcement_lines) if enforcement_lines else ""

        # Include last vision analysis prominently
        last_vis = goal.get("last_vision_analysis", "")
        vision_context = ""
        if last_vis:
            vision_context = f"\n\nLAST SCREENSHOT ANALYSIS (what is currently visible on screen):\n{last_vis[:2000]}\n"

        planner_prompt = (
            f"{user_profile}\n\n"
            f"GOAL OBJECTIVE: \"{goal['objective']}\"\n"
            f"SUCCESS CRITERIA: \"{goal['success_criteria']}\"\n"
            f"CURRENT PROGRESS (Step {iter_num} of {max_iterations}):\n"
            f"Real actions executed so far: {real_action_count} (minimum {MIN_REAL_ACTIONS_BEFORE_COMPLETE} required before COMPLETE)\n"
            f"{subgoals_text}\n"
            f"{enforcement_block}\n"
            f"{vision_context}\n"
            f"AVAILABLE TOOLS:\n{json.dumps(manifest, indent=2)}\n\n"
            f"ACTION HISTORY & DIAGNOSTICS SO FAR:\n"
            f"{history_summary}\n\n"
            f"Based on the action history and last screen analysis:\n"
            f"- If ALL success criteria are confirmed by the MOST RECENT screenshot's vision analysis, emit COMPLETE.\n"
            f"- Otherwise: determine the exact next action to advance the active subgoal.\n"
            f"- If no screenshot has been taken yet, your first action MUST be take_screenshot.\n"
            f"- If your last action was a click/type/key, your next action SHOULD be take_screenshot to verify it worked.\n"
            f"- If your last action was take_screenshot and the goal is NOT yet achieved, your next action MUST be a real interaction (click, type, key, focus, sys_exec)."
        )

        try:
            route_res = await generate("heavy", planner_prompt, system=GOAL_PLANNER_SYSTEM_PROMPT)
            if cancel_event.is_set():
                goal["status"] = "stopped"
                return goal
            decision = _parse_json_robust(route_res.get("text", ""))
            print(f"[GoalManager] Step {iter_num}: Model returned action={decision.get('action', 'UNKNOWN')} "
                  f"(provider={route_res.get('provider')}/{route_res.get('model')})", flush=True)
        except Exception as e:
            print(f"[GoalManager] Planning error at step {iter_num}: {e}", flush=True)
            decision = {}

        action = decision.get("action", "")
        thought = decision.get("thought", "")

        # ----------------------------------------------------------------
        # FALLBACK: If model returned empty or no valid action, pick a
        # sensible default based on what has happened so far.
        # ----------------------------------------------------------------
        if not action or action not in ("USE_TOOL", "COMPLETE", "MAKE_TOOL"):
            if not goal["tool_trace"]:
                # Nothing done yet → take screenshot first
                decision = {"action": "USE_TOOL", "tool_name": "take_screenshot", "args": {}}
                action = "USE_TOOL"
                print(f"[GoalManager] Step {iter_num}: Fallback → take_screenshot (no history)", flush=True)
            elif goal["tool_trace"][-1]["tool"] == "take_screenshot":
                # Last action was screenshot → we need a real action.
                # Try to infer from the vision analysis.
                decision = _infer_action_from_context(goal)
                action = decision.get("action", "USE_TOOL")
                print(f"[GoalManager] Step {iter_num}: Fallback → {decision.get('tool_name', '?')} (after screenshot)", flush=True)
            else:
                # Last action was something else → take screenshot to verify
                decision = {"action": "USE_TOOL", "tool_name": "take_screenshot", "args": {}}
                action = "USE_TOOL"
                print(f"[GoalManager] Step {iter_num}: Fallback → take_screenshot (verify last action)", flush=True)

        # Update any subgoals modified by model
        for usg in decision.get("update_subgoals", []):
            idx = usg.get("index")
            new_st = usg.get("status")
            if idx is not None and 0 <= idx < len(goal["subgoals"]) and new_st:
                goal["subgoals"][idx]["status"] = new_st

        # ----------------------------------------------------------------
        # COMPLETE GATE: Enforce anti-bluff rules before accepting COMPLETE
        # ----------------------------------------------------------------
        if action == "COMPLETE":
            blocked = False
            block_reason = ""

            if iter_num <= 2:
                blocked = True
                block_reason = f"Step {iter_num} is too early (minimum step 3)"
            elif real_action_count < MIN_REAL_ACTIONS_BEFORE_COMPLETE:
                blocked = True
                block_reason = (
                    f"Only {real_action_count} real actions executed "
                    f"(need {MIN_REAL_ACTIONS_BEFORE_COMPLETE})"
                )
            elif not _last_vision_confirms_criteria(goal["tool_trace"], goal["success_criteria"]):
                blocked = True
                block_reason = "Last screenshot vision analysis does NOT confirm success criteria"

            if blocked:
                print(
                    f"[GoalManager] Step {iter_num}: ⛔ COMPLETE BLOCKED — {block_reason}. "
                    f"Forcing continuation.", flush=True
                )
                # Override: if last action was screenshot, do a real action; otherwise screenshot
                if goal["tool_trace"] and goal["tool_trace"][-1]["tool"] == "take_screenshot":
                    decision = _infer_action_from_context(goal)
                else:
                    decision = {"action": "USE_TOOL", "tool_name": "take_screenshot", "args": {}}
                action = "USE_TOOL"
            else:
                # COMPLETE is genuinely earned
                goal["status"] = "completed"
                goal["summary"] = decision.get("summary") or f"Successfully completed objective: {goal['objective']}"
                goal["verification_evidence"] = decision.get("verification_evidence") or "All milestones executed and verified."
                print(f"[GoalManager] 🎉 Goal completed at step {iter_num}: {goal['summary'][:100]}", flush=True)

                # Mark all remaining subgoals as completed
                for sg in goal["subgoals"]:
                    if sg["status"] != "completed":
                        sg["status"] = "completed"

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

        # ----------------------------------------------------------------
        # MAKE_TOOL
        # ----------------------------------------------------------------
        if action == "MAKE_TOOL":
            t_name = decision.get("tool_name", f"tool_step_{iter_num}")
            t_desc = decision.get("description", goal["objective"])
            tool_args = decision.get("args", {})
            try:
                build_res = await create_and_run_tool(t_name, t_desc, tool_args)
                goal["tool_trace"].append({"tool": t_name, "args": tool_args, "output": build_res.get("output", "")})
            except Exception as be:
                goal["tool_trace"].append({"tool": t_name, "args": tool_args, "output": f"Tool build error: {be}"})
            continue

        # ----------------------------------------------------------------
        # USE_TOOL
        # ----------------------------------------------------------------
        if action == "USE_TOOL":
            t_name = decision.get("tool_name", "")
            tool_args = decision.get("args", {})
            if not t_name:
                t_name = "take_screenshot"
                tool_args = {}

            # Prevent screenshot loops — if we already took 2+ screenshots in a row
            # without any real action, force a real action instead.
            if t_name == "take_screenshot":
                consecutive_screenshots += 1
                if consecutive_screenshots > MAX_CONSECUTIVE_SCREENSHOTS:
                    print(f"[GoalManager] Step {iter_num}: Breaking screenshot loop — forcing real action", flush=True)
                    decision = _infer_action_from_context(goal)
                    t_name = decision.get("tool_name", "desktop_navigator")
                    tool_args = decision.get("args", {})
                    consecutive_screenshots = 0
            else:
                consecutive_screenshots = 0

            print(
                f"[GoalManager] Step {iter_num}: Running '{t_name}' with {tool_args} "
                f"(Thought: {thought[:80]}...)",
                flush=True,
            )

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
                            f"Analyze this active desktop screenshot and answer:\n"
                            f"1. What application window is currently focused and visible?\n"
                            f"2. What is the current state of the screen? Describe key UI elements.\n"
                            f"3. Is the goal objective ('{goal['objective']}') currently satisfied on screen? "
                            f"Answer YES or NO and explain specifically what you see.\n"
                            f"4. If NOT satisfied: What exact action should be taken next? "
                            f"Provide specific coordinates (X, Y) for any click targets you can identify.\n"
                            f"5. List any error dialogs, popups, or unexpected states visible on screen."
                        )
                        vis_res = await generate("vision", vis_prompt, image_data=b64, mime_type="image/png")
                        vis_analysis = vis_res.get("text", "")
                        goal["last_vision_analysis"] = vis_analysis
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


def _infer_action_from_context(goal: dict) -> dict:
    """When the model returns garbage or COMPLETE is blocked, infer a sensible
    next action from the current goal state and last vision analysis.

    This is a rule-based fallback — not AI — so it's fast and deterministic.
    """
    objective_lower = goal["objective"].lower()
    last_vis = goal.get("last_vision_analysis", "").lower()
    real_actions = _count_real_actions(goal["tool_trace"])

    # If no real actions yet, the first thing to do is focus/launch the app
    if real_actions == 0:
        # Detect what app the goal needs
        if any(kw in objective_lower for kw in ["chrome", "youtube", "yt", "video", "play"]):
            return {
                "action": "USE_TOOL",
                "tool_name": "sys_exec",
                "args": {"command": "google-chrome 'https://www.youtube.com' &"},
                "thought": "Launching Chrome with YouTube as the first real action"
            }
        elif any(kw in objective_lower for kw in ["zen", "browser", "web"]):
            return {
                "action": "USE_TOOL",
                "tool_name": "desktop_navigator",
                "args": {"action": "focus", "text": "zen"},
                "thought": "Focusing Zen Browser"
            }
        elif any(kw in objective_lower for kw in ["file", "folder", "directory", "nautilus"]):
            return {
                "action": "USE_TOOL",
                "tool_name": "sys_exec",
                "args": {"command": "nautilus ~ &"},
                "thought": "Opening file manager"
            }
        elif any(kw in objective_lower for kw in ["terminal", "command", "exec", "install"]):
            return {
                "action": "USE_TOOL",
                "tool_name": "sys_exec",
                "args": {"command": "echo 'Ready to execute commands'"},
                "thought": "Testing command execution"
            }
        else:
            # Generic: take a screenshot to see what's going on
            return {
                "action": "USE_TOOL",
                "tool_name": "take_screenshot",
                "args": {},
                "thought": "Taking initial screenshot to assess state"
            }

    # If we have a vision analysis suggesting what to click/do, try to act on it
    if "search" in objective_lower and last_vis:
        # Need to type something — find out what
        search_terms = objective_lower
        for strip_word in ["play", "open", "search", "find", "on youtube", "in chrome",
                           "on yt", "in google chrome", "goal", "and"]:
            search_terms = search_terms.replace(strip_word, "")
        search_terms = search_terms.strip()
        if search_terms:
            return {
                "action": "USE_TOOL",
                "tool_name": "desktop_navigator",
                "args": {"action": "type", "text": search_terms},
                "thought": f"Typing search query: {search_terms}"
            }

    # Default: if the last tool was a screenshot, try clicking somewhere relevant
    # based on the vision analysis. If we don't know where, focus Chrome.
    if "chrome" in last_vis or "google" in last_vis:
        return {
            "action": "USE_TOOL",
            "tool_name": "desktop_navigator",
            "args": {"action": "click", "x": 960, "y": 500},
            "thought": "Clicking center of screen to interact with visible content"
        }

    # Last resort: focus Chrome if the goal seems browser-related
    if any(kw in objective_lower for kw in ["chrome", "youtube", "yt", "video", "play",
                                             "web", "browser", "search"]):
        return {
            "action": "USE_TOOL",
            "tool_name": "desktop_navigator",
            "args": {"action": "focus", "text": "google-chrome"},
            "thought": "Focusing Chrome as fallback action"
        }

    # Very generic fallback
    return {
        "action": "USE_TOOL",
        "tool_name": "take_screenshot",
        "args": {},
        "thought": "Taking screenshot to reassess current state"
    }
