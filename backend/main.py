import re
import os
import json
import base64
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from core.config import Config
from core.router import generate
from core.database import load_local_manifest, save_local_manifest, sync_tools_from_cloud
from core.sandbox import run_tool, create_and_run_tool
from core.memory import add_message, format_history_context, clear_session, load_session, get_history, save_upload_image, copy_file_to_uploads, extract_image_path, UPLOADS_DIR
from core.user_profile import get_user_profile_context, load_profile
from core.evolution import reflect_and_evolve

ROUTER_SYSTEM_PROMPT = """You are the Lead Orchestrator for Muse on Ubuntu Linux with direct system access via tools/.
Analyze the user request, recent conversation history, and available tools.

HERMES-STYLE BROWSER & DESKTOP ROUTING RULES:
1. WEB BROWSING & YOUTUBE / MEDIA PLAYBACK (USE browser_agent):
   - For ANY YouTube music, song, or video request: IMMEDIATELY call `browser_agent` with:
     {"action": "USE_TOOL", "tool_name": "browser_agent", "args": {"action": "play_youtube", "query": "<exact song or video title>"}}
     This automatically navigates, searches, clicks the first video, and ensures playback in visible Chrome.
   - For ANY website navigation or web interaction:
     * Step 1: Call `browser_agent` with {"action": "goto", "url": "https://..."} or {"action": "inspect"} to read the `interactive_elements` list ([{"id": 1, "tag": "button", "text": "..."}]).
     * Step 2: Call `browser_agent` with {"action": "click", "id": <number>} using the exact element ID from `interactive_elements`, or {"action": "type", "id": <number>, "text": "..."} to type into inputs.
   - DO NOT use desktop coordinate clicking (`desktop_navigator`) for websites or browser tabs when `browser_agent` can click precise DOM elements directly.

2. NATIVE DESKTOP APPLICATIONS & SYSTEM (USE desktop_navigator & sys_exec):
   - Reserve `desktop_navigator` STRICTLY for non-browser native Linux desktop applications (such as file manager, GNOME settings, native media players, or terminal windows) or desktop keyboard shortcuts.
   - SCREEN RESOLUTION: Exactly 1920x1080 pixels (X: 0 to 1920, Y: 0 to 1080).
   - DOCK & TOP PANEL BOUNDARIES: The Ubuntu GNOME top bar is at Y < 40 and the application dock is at the bottom (Y > 980). NEVER click Y > 980 or Y < 40 for application interactions!
   - Step 1 (Inspect Screen): Call `take_screenshot` ONCE.
   - Step 2 (Focus & Execute): Read `ai_vision_analysis`, then focus or click with `desktop_navigator`.
   - Step 3 (Confirm): Call `take_screenshot` ONCE to confirm.
   - Limit: Max 2 screenshots per turn.

Return ONLY a valid JSON object matching ONE of these actions:

1. EXECUTE A TOOL (Next step in your plan):
{"action": "USE_TOOL", "tool_name": "exact_name_from_available_tools", "args": {"key": "value"}}
Common tools:
- browser_agent: Hermes-style persistent visible Chrome controller via CDP. Use for ALL web pages, YouTube playback, and web searches:
  * YouTube Play: {"action": "play_youtube", "query": "song name"}
  * Navigate: {"action": "goto", "url": "https://..."}
  * Inspect page & elements: {"action": "inspect"}
  * Click element by ID: {"action": "click", "id": 12}
  * Type into element: {"action": "type", "id": 5, "text": "...", "submit": true}
  * Scroll: {"action": "scroll", "direction": "down"}
- desktop_navigator: Control user's REAL visible desktop screen (1920x1080), mouse, and keyboard for NATIVE apps.
  * Actions: 'focus', 'click', 'find_and_click', 'type', 'key', 'minimize'.
- take_screenshot: Captures active Linux desktop display and automatically runs AI vision analysis to report visible windows, UI elements, and coordinates.
- sys_exec: Safely run any bash shell command with a 15-second timeout: {"command": "..."}
- web_search: Searches the web for live information, news, links: {"query": "..."}
- media_control: Controls audio volume, mute, WirePlumber sinks, and Bluetooth: {"action": "status|volume_up|volume_down|mute"}
- git_manager: Inspects git repositories (status, log, diff, branch): {"action": "status|log|branch", "path": "..."}
- system_monitor: Proactive health monitor (CPU, RAM %, Disks, Top processes, Battery): {}
- fs_list: Lists files & directories in a path: {"path": "..."}
- fs_read: Reads file text content: {"path": "..."}

2. COMPLETE (All necessary info collected, ready to answer):
{"action": "FINISH"}
Use this when previous tool steps have gathered everything needed to answer the user request.

3. CREATE NEW TOOL (Strict Last Resort):
{"action": "MAKE_TOOL", "tool_name": "short_snake_case_name", "description": "Specification of what the script must do", "args": {}}
CRITICAL: NEVER create a new tool for tasks that can be solved by running 'browser_agent', 'desktop_navigator', 'sys_exec', or existing tools!

4. TASK CONTINUATIONS & CONFIRMATIONS:
If the conversation history shows an active automated task (such as inspecting a webpage, playing music, or follow-up confirmations like 'done', 'try now', 'go on', 'check'):
CRITICAL: NEVER return 'DIRECT'! You MUST emit 'USE_TOOL' to execute!

5. DIRECT CONVERSATION (Only when truly purely conversational):
{"action": "DIRECT"}
Use ONLY for pure chit-chat, greetings, or conceptual questions where NO system, web, or pending task exists.

6. MANDATORY MULTI-MODAL VERIFICATION (NEVER BLUFF COMPLETION):
   - You MUST NEVER emit {"action": "FINISH"} after performing an action without verifying its real-world outcome!
   - BROWSER / YOUTUBE: `browser_agent` returns the playback state, current URL, and an updated screenshot proving the video is playing or page is loaded.
   - DESKTOP / GUI / WINDOWS: If you just ran 'desktop_navigator', verify with 'take_screenshot'.
   - SYSTEM / PROCESSES / FILES: Verify with 'sys_exec' or 'fs_read'.
   - Only when verification confirms the action, or if verification reveals what actually happened, emit {"action": "FINISH"}.

Note: Always format 'args' as a JSON dictionary. Return raw JSON only."""

SYNTHESIS_SYSTEM_PROMPT = """You are Muse, an autonomous self-extending and self-healing AI agent running locally on the user's Linux machine with direct system access via generated Python tools in tools/.
You have real, direct access to the local operating system, files, and terminal environment through your executed tools.

CORE IDENTITY & GUIDELINES:
1. IDENTITY: You are Muse—an intelligent, autonomous local agent. Never identify as ChatGPT, OpenAI, Claude, or a generic cloud model.
2. LOCAL ACCESS: You have direct local filesystem and OS capabilities via your Python sandbox and tool registry. NEVER say "I am a cloud model and cannot access your local file system."
3. PROJECT LOCATION: Your source code lives at `~/Documents/muse-agent`. The backend is at `~/Documents/muse-agent/backend`, tools are at `~/Documents/muse-agent/backend/tools`, and environment configurations are in `~/Documents/muse-agent/backend/.env`. You know this location and navigate it seamlessly.
4. SCREENSHOTS & IMAGES: When a screenshot or photo tool runs, the application runtime automatically delivers the captured image directly into the user's chat. NEVER ask the user for a Telegram Bot Token, Chat ID, or credentials to send photos—you are already running inside the chat interface.
5. NO FALSE PROMISES OF FUTURE MESSAGES: This response is the ONLY message delivered for this turn. NEVER say "Give me a moment", "Hold on while I pull the data", "I will get back to you", or "I'm working on it now". State the exact completed findings right now, or explain the exact status and next step immediately.
6. SELF-HEALING & EVOLUTION: You dynamically write your own tools, auto-resolve and install missing libraries, and adapt when errors occur.
7. ERROR RESOLUTION: If a tool fails or reports '[Errno 2] No such file or directory', explain clearly which path was searched, and suggest the user verify the exact folder name or pass an absolute path.
8. NO HALLUCINATED TASK COMPLETION / MANDATORY VERIFICATION: NEVER claim a song/video is playing, a window is closed/minimized, or a task is finished unless the tool trace has concrete verification evidence (e.g. follow-up screenshot or process status). If you attempted an action without follow-up verification, explicitly state: "Attempted action X, but unverified on screen/system."
9. COMPLETE TOOL ERROR TRANSPARENCY: If a tool failed, crashed, or reported an error, NEVER conceal it or pretend success. Explain the exact error, what failed, and the direct next step.
10. TONE: Sharp, technical, concise, helpful, and proactive."""

TELEGRAM_SYNTHESIS_SYSTEM_PROMPT = """You are Muse, an autonomous self-extending and self-healing AI agent running locally on the user's Linux machine, chatting with the user via Telegram on mobile.

TELEGRAM MOBILE FORMATTING RULES:
1. MOBILE-FIRST FORMATTING: Format your response specifically for a mobile chat app. Keep it structured, punchy, and quick to read.
2. NO FALSE PROMISES: This response is the ONLY message the user will receive for this turn. NEVER say "Give me a moment", "Hold on while I pull the data", "I'll get back to you", or "I'm checking it now". NEVER end your response promising future updates, live checks, or saying you will fetch data later. All tool executions for this turn have already completed. Deliver the concrete facts, numbers, or current findings right now.
3. NO MARKDOWN TABLES: NEVER generate Markdown tables (| col | col |). Telegram mobile clients cannot render tables and they look broken and unreadable. Instead, use clean emoji-bulleted lists:
   - 📁 **folder_name/**
   - 📄 **file_name** (size)
   - ⚡ **Key:** Value
4. NO '#' HEADINGS: Telegram does not support # headings. Use bold titles with emojis instead (e.g. ⚡ **System Overview**, 📁 **Directory Contents**).
5. CODE & PATHS: Use inline code `path/to/file` or triple-backtick code blocks for scripts or output.
6. REAL SYSTEM ACCESS: You have direct local filesystem and OS access on Ubuntu Linux. Never claim to be a cloud model.
7. NEVER ASK FOR TOKENS: You are already speaking inside the Telegram bot. Never ask the user for a Telegram bot token, chat ID, or credentials to send photos or messages.
8. NO HALLUCINATIONS / GROUND TRUTH VERIFICATION: NEVER claim a song/video is playing, a window is minimized, or a command worked unless confirmed by tool outputs or screenshots. If unverified, state: "Attempted action X, but unverified on screen."
9. COMPLETE TOOL ERROR TRANSPARENCY: If a tool crashed or reported an error, tell the user the exact error details and what failed rather than bluffing."""

def _parse_json(text: str) -> dict:
    if not text:
        return {"action": "DIRECT"}

    cleaned = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", text, flags=re.DOTALL).strip()
    decoder = json.JSONDecoder()
    parsed_obj = None

    for i, ch in enumerate(cleaned):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(cleaned[i:])
                if isinstance(obj, dict):
                    parsed_obj = obj
                    break
            except Exception:
                continue

    if not parsed_obj:
        return {"action": "DIRECT"}

    # Handle standard nested function calling: {"function": {"name": ..., "arguments": ...}}
    if "function" in parsed_obj and isinstance(parsed_obj["function"], dict):
        fn = parsed_obj["function"]
        return {
            "action": "USE_TOOL",
            "tool_name": fn.get("name"),
            "args": fn.get("arguments", fn.get("parameters", {})),
        }

    # Handle tool/name call without action: {"name": "web_search", "parameters": ...}
    if ("name" in parsed_obj or "tool" in parsed_obj) and "action" not in parsed_obj:
        t_name = parsed_obj.get("name") or parsed_obj.get("tool")
        args = parsed_obj.get("parameters") or parsed_obj.get("arguments") or parsed_obj.get("args") or {}
        if not args:
            args = {k: v for k, v in parsed_obj.items() if k not in ("name", "tool", "tool_name")}
        return {
            "action": "USE_TOOL",
            "tool_name": t_name,
            "args": args if isinstance(args, dict) else {},
        }

    action = parsed_obj.get("action")
    if not action:
        if "tool_name" in parsed_obj or "tool" in parsed_obj:
            t_name = parsed_obj.get("tool_name") or parsed_obj.get("tool")
            args = parsed_obj.get("args", {})
            if not args:
                args = {k: v for k, v in parsed_obj.items() if k not in ("tool", "tool_name")}
            return {
                "action": "USE_TOOL",
                "tool_name": t_name,
                "args": args if isinstance(args, dict) else {},
            }
        return {"action": "DIRECT"}

    action_upper = str(action).strip().upper()
    if action_upper in ("FINISH", "COMPLETE", "DONE"):
        return {"action": "FINISH"}
    if action_upper in ("DIRECT", "CHAT", "CONVERSE"):
        return {"action": "DIRECT"}
    if action_upper in ("MAKE_TOOL", "CREATE_TOOL", "BUILD_TOOL"):
        return {
            "action": "MAKE_TOOL",
            "tool_name": parsed_obj.get("tool_name", "custom_tool"),
            "description": parsed_obj.get("description", ""),
            "args": parsed_obj.get("args", {}),
        }
    if action_upper in ("USE_TOOL", "RUN_TOOL", "EXECUTE_TOOL", "TOOL"):
        t_name = parsed_obj.get("tool_name") or parsed_obj.get("tool") or parsed_obj.get("name")
        args = parsed_obj.get("args", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"raw": args}
        if not isinstance(args, dict):
            args = {}
        if not args:
            args = {k: v for k, v in parsed_obj.items() if k not in ("action", "tool_name", "tool", "name")}
        return {
            "action": "USE_TOOL",
            "tool_name": t_name,
            "args": args,
        }

    # Model emitted action as the tool name itself (e.g. {"action": "sys_exec", "command": "date"})
    t_name = action
    args = parsed_obj.get("args", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {"raw": args}
    if not isinstance(args, dict) or not args:
        args = {k: v for k, v in parsed_obj.items() if k != "action"}
    return {
        "action": "USE_TOOL",
        "tool_name": t_name,
        "args": args,
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    await sync_tools_from_cloud()
    yield

app = FastAPI(title="Muse Self-Extending Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/api/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/tools")
def get_tools():
    return load_local_manifest()

@app.get("/api/tools/{name}/code")
def get_tool_code(name: str):
    script_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
    if not os.path.exists(script_path):
        raise HTTPException(status_code=404, detail="Tool code not found")
    with open(script_path, "r", encoding="utf-8") as f:
        return {"name": name, "code": f.read()}

@app.delete("/api/tools/{name}")
def delete_tool(name: str):
    manifest = load_local_manifest()
    if name in manifest:
        del manifest[name]
        save_local_manifest(manifest)
    
    script_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
    if os.path.exists(script_path):
        os.remove(script_path)
    return {"status": "deleted", "tool": name}

@app.get("/api/chat/history")
def get_chat_history(session_id: str = "web_default"):
    return load_session(session_id)

@app.post("/api/chat/clear")
def clear_history(session_id: str = "web_default"):
    clear_session(session_id)
    return {"status": "cleared"}

@app.get("/api/profile")
def get_profile():
    return load_profile()

class ChatRequest(BaseModel):
    prompt: str = ""
    image: str | None = None
    mime_type: str | None = "image/jpeg"
    session_id: str | None = "web_default"

async def _stream_chat(prompt: str, image: str | None = None, mime_type: str = "image/jpeg", session_id: str = "web_default"):
    session = session_id or "web_default"
    async def event_generator():
        try:
            # 1. Vision mode: image analysis
            if image:
                yield {"event": "status", "data": json.dumps({"step": "Analyzing image with Muse Vision..."})}
                vision_prompt = prompt.strip() or "Analyze this image and describe what you see in detail."
                final = await generate(
                    "vision",
                    vision_prompt,
                    system="You are an expert AI vision analyst. Inspect the image closely and provide clear, accurate, and structured observations.",
                    image_data=image,
                    mime_type=mime_type or "image/jpeg",
                    history=get_history(session, limit=6),
                )
                image_url = save_upload_image(image, mime_type or "image/jpeg")
                add_message(session, "user", vision_prompt, extra={"image": image_url} if image_url else None)
                add_message(session, "assistant", final["text"])
                yield {
                    "event": "result",
                    "data": json.dumps({
                        "text": final["text"],
                        "model": f"{final['provider']}/{final['model']}",
                    }),
                }
                return

            # 2. Multi-step agent execution loop
            manifest = load_local_manifest()
            history_context = format_history_context(session)
            user_profile_context = get_user_profile_context()

            max_steps = 4
            step_count = 0
            tool_trace = []
            combined_raw = []
            images_collected = []
            created_tool_name = None

            yield {"event": "status", "data": json.dumps({"step": "Analyzing request & planning...", "tools_count": len(manifest)})}

            while step_count < max_steps:
                step_count += 1
                tool_names = list(manifest.keys())
                steps_left = max(0, max_steps - step_count)
                tools_context = f"LOCAL TOOL REGISTRY: {len(tool_names)} tools loaded ({', '.join(tool_names)})\nEXECUTION BUDGET: Step {step_count} of {max_steps} ({steps_left} steps remaining in this turn)"

                if not tool_trace:
                    planner_prompt = (
                        f"{user_profile_context}\n\n"
                        f"{tools_context}\n\n"
                        f"Conversation History:\n{history_context}\n\n"
                        f"Available Tools Schema:\n{json.dumps(manifest)}\n\n"
                        f"User Request: {prompt}"
                    )
                else:
                    steps_summary = "\n".join([
                        f"- Step {i+1}: Ran '{st['tool']}' with args {st['args']} -> Output & Diagnostics:\n{st['output'] if len(st['output']) <= 3500 else (st['output'][:2000] + '\n...[output truncated]...\n' + st['output'][-1500:])}"
                        for i, st in enumerate(tool_trace)
                    ])
                    planner_prompt = (
                        f"{user_profile_context}\n\n"
                        f"{tools_context}\n\n"
                        f"Conversation History:\n{history_context}\n\n"
                        f"Available Tools Schema:\n{json.dumps(manifest)}\n\n"
                        f"Steps already completed in this turn:\n{steps_summary}\n\n"
                        f"Original User Request: {prompt}\n\n"
                        f"Based on completed steps and output above, decide the next action: USE_TOOL, FINISH, or MAKE_TOOL (Remember: always verify desktop/system actions before finishing!):"
                    )

                route_res = await generate("router", planner_prompt, system=ROUTER_SYSTEM_PROMPT)
                decision = _parse_json(route_res["text"])
                action = decision.get("action", "FINISH" if tool_trace else "DIRECT")

                if action == "FINISH":
                    break

                if action == "DIRECT":
                    if not tool_trace:
                        direct_sys = f"{SYNTHESIS_SYSTEM_PROMPT}\n\n{user_profile_context}\n\nYour active local tools in tools/manifest.json:\n{json.dumps(manifest, indent=2)}"
                        ans = await generate("chat", prompt, system=direct_sys, history=get_history(session, limit=10))
                        add_message(session, "user", prompt)
                        add_message(session, "assistant", ans["text"])
                        yield {"event": "result", "data": json.dumps({"text": ans["text"], "model": f"{ans['provider']}/{ans['model']}"})}
                        return
                    else:
                        break

                if action == "USE_TOOL":
                    t_name = decision.get("tool_name", "")
                    tool_args = decision.get("args", {})
                    if not t_name:
                        break
                    yield {
                        "event": "status",
                        "data": json.dumps({
                            "step": f"Step {step_count}: Running '{t_name}'...",
                            "decision": decision,
                            "router_model": f"{route_res['provider']}/{route_res['model']}",
                            "tools_count": len(manifest),
                        }),
                    }

                    success, output = run_tool(t_name, tool_args)
                    if t_name == "take_screenshot" and success:
                        img_path = extract_image_path(output)
                        if img_path and os.path.isfile(img_path):
                            vis_prompt = (
                                f"You are the visual cortex for Muse Agent on Ubuntu Linux desktop.\n"
                                f"The user's current request is: '{prompt}'\n\n"
                                f"SCREEN METRICS & SPATIAL GROUNDING:\n"
                                f"- Total Display Resolution: EXACTLY 1920x1080 pixels (Width: 1920, Height: 1080).\n"
                                f"- Coordinate System: Absolute pixel coordinates from (0, 0) top-left to (1920, 1080) bottom-right.\n"
                                f"- DO NOT use normalized 0-1000 or 0.0-1.0 coordinates! Output exact integer pixel coordinates (X: 0 to 1920, Y: 0 to 1080).\n"
                                f"- CRITICAL BOUNDARY & PANEL CONSTRAINTS:\n"
                                f"  * Top Panel: Y < 40 is the Ubuntu system top bar (clock, indicators). NEVER output coordinates with Y < 40 for application interactions.\n"
                                f"  * Bottom Dock: Y > 980 contains the Ubuntu system application dock (Files/Nautilus, Terminal, Settings). NEVER output Y > 980 unless explicitly asked to interact with the system dock.\n"
                                f"  * Safe Application Canvas: Web pages, YouTube video cards, search bars, and application windows reside strictly between X: 50 to 1870 and Y: 80 to 950.\n\n"
                                f"Analyze this active desktop screenshot carefully:\n"
                                f"1. What application/window is currently focused and active in the foreground?\n"
                                f"2. Is the requested application (e.g. Google Chrome, YouTube, Zen Browser) visible on screen? Where is it located?\n"
                                f"3. What are the key UI elements visible (e.g. search bars, video thumbnails/cards, play buttons, tabs) and their exact pixel coordinates (X, Y) within the 1920x1080 resolution?\n"
                                f"4. What specific action should the agent take next (e.g. 'Launch or focus Google Chrome first via desktop_navigator focus because only VS Code is visible', or 'Click YouTube video title at pixel (X: 620, Y: 340)')?\n"
                                f"Be concise, technical, and actionable."
                            )
                            try:
                                with open(img_path, "rb") as f:
                                    b64 = base64.b64encode(f.read()).decode("utf-8")
                                vis_res = await generate("vision", vis_prompt, image_data=b64, mime_type="image/png")
                                vis_analysis = vis_res.get("text", "")
                                output = json.dumps({
                                    "status": "success",
                                    "file": img_path,
                                    "ai_vision_analysis": vis_analysis
                                })
                            except Exception as ve:
                                print(f"[Main] Vision analysis error on screenshot: {ve}", flush=True)

                    tool_trace.append({"tool": t_name, "args": tool_args, "output": output if success else f"TOOL ERROR: {output}"})
                    combined_raw.append(f"[{t_name}]:\n{output}")
                    if success:
                        img = extract_image_path(output)
                        if img and img not in images_collected:
                            images_collected.append(img)
                    else:
                        yield {"event": "status", "data": json.dumps({"step": f"Tool '{t_name}' returned error, re-planning..."})}
                    continue

                if action == "MAKE_TOOL":
                    t_name = decision.get("tool_name", "custom_tool")
                    t_desc = decision.get("description", prompt)
                    tool_args = decision.get("args", {})
                    yield {"event": "status", "data": json.dumps({"step": f"Synthesizing new tool '{t_name}'..."})}

                    build_res = await create_and_run_tool(t_name, t_desc, tool_args)
                    if build_res["success"]:
                        created_tool_name = t_name
                        tool_trace.append({"tool": t_name, "args": tool_args, "output": build_res["output"]})
                        combined_raw.append(f"[{t_name}]:\n{build_res['output']}")
                        img = extract_image_path(build_res["output"])
                        if img and img not in images_collected:
                            images_collected.append(img)
                        break
                    else:
                        yield {"event": "error", "data": json.dumps({"error": build_res["output"]})}
                        return

            # Synthesize final answer from all gathered tool outputs
            yield {"event": "status", "data": json.dumps({"step": "Synthesizing final response..."})}
            steps_context = "\n\n---\n\n".join(combined_raw)
            final = await generate(
                "chat",
                f"{user_profile_context}\n\nUser asked: {prompt}\n\nExecuted tool results across steps:\n{steps_context if steps_context else '(No tools were executed)'}\n\nSynthesize a clear, structured, and complete final answer for the user. Remember: State exact verification evidence truthfully, report any tool errors completely, and never bluff task completion.",
                system=SYNTHESIS_SYSTEM_PROMPT,
                history=get_history(session, limit=10),
            )
            final_text = final["text"]
            web_img_url = ""
            for img in images_collected:
                url = copy_file_to_uploads(img)
                if url:
                    web_img_url = url
                    if url not in final_text:
                        final_text = f"{final_text}\n\n![Captured Image]({url})"

            add_message(session, "user", prompt)
            add_message(session, "assistant", final_text, extra={"image": web_img_url} if web_img_url else None)

            # Hermes autonomous evolution: reflect on this turn in background
            asyncio.create_task(reflect_and_evolve(prompt, final_text, tool_trace))
            yield {
                "event": "result",
                "data": json.dumps({
                    "text": final_text,
                    "raw_tool_output": "\n\n---\n\n".join(combined_raw),
                    "tool_created": created_tool_name,
                    "model": f"{final['provider']}/{final['model']}",
                }),
            }
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())

@app.get("/api/chat/stream")
async def chat_stream_get(prompt: str, session_id: str = "web_default"):
    return await _stream_chat(prompt, session_id=session_id)

@app.post("/api/chat/stream")
async def chat_stream_post(req: ChatRequest):
    return await _stream_chat(req.prompt, req.image, req.mime_type or "image/jpeg", session_id=req.session_id or "web_default")