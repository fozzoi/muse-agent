import re
import os
import json
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
from core.user_profile import get_user_profile_context
from core.evolution import reflect_and_evolve

ROUTER_SYSTEM_PROMPT = """You are the Lead Orchestrator for Muse on Ubuntu Linux with direct system access via tools/.
Analyze the user request, recent conversation history, and available tools.

ROUTING RULES FOR BROWSER & DESKTOP:
1. DESKTOP / VISIBLE MEDIA CONTROL:
   - If the user asks to play a song/video, click something visible, navigate their open Chrome/YouTube window, or interact with an active desktop application:
   - NEVER use `browser_agent`.
   - First use `take_screenshot` if you need to inspect the screen.
   - Use `desktop_navigator` with action='find_and_click' (passing target_text) or action='type'/'key' to interact with the real window.

2. SILENT BACKGROUND SCRAPING:
   - Use `browser_agent` ONLY when the user explicitly requests silent web data extraction, scraping, or reading web pages in the background where the user DOES NOT need to see the screen or hear playback.
   - DO NOT use `browser_agent` when interacting with the user's open desktop windows.

Return ONLY a valid JSON object matching ONE of these actions:

1. EXECUTE A TOOL (Next step in your plan):
{"action": "USE_TOOL", "tool_name": "exact_name_from_available_tools", "args": {"key": "value"}}
Common tools:
- desktop_navigator: Control user's REAL visible desktop screen, mouse, and keyboard. Use when interacting with desktop windows, Chrome, YouTube, or clicking visible elements. Actions: 'find_and_click' (args: {'target_text': 'song or button'}), 'click' (args: {'x': 100, 'y': 200}), 'type' (args: {'text': '...'}), 'key' (args: {'text': 'Return'}).
- browser_agent: SILENT HEADLESS background browser. Use ONLY for silent background scraping or data extraction where user does not need to see screen or hear playback. NEVER use for desktop media/visible windows: {"action": "navigate|scrape|click|type", "url": "..."}
- take_screenshot: Captures active Linux desktop display to inspect what windows, videos, or apps are currently visible: {}
- sys_exec: Safely run any bash shell command (e.g. ps, uptime, curl, cli queries): {"command": "..."}
- web_search: Searches the web for live information, news, links: {"query": "..."}
- media_control: Controls audio volume, mute, WirePlumber sinks, and Bluetooth: {"action": "status|volume_up|volume_down|mute"}
- git_manager: Inspects git repositories (status, log, diff, branch): {"action": "status|log|branch", "path": "..."}
- system_monitor: Proactive health monitor (CPU, RAM %, Disks, Top processes, Battery): {}
- fs_list: Lists files & directories in a path: {"path": "..."}
- fs_read: Reads file text content: {"path": "..."}
- run_tesseract_ocr: Extracts text from images/screenshots: {"path": "..."}

2. COMPLETE (All necessary info collected, ready to answer):
{"action": "FINISH"}
Use this when previous tool steps have gathered everything needed to answer the user request.

3. CREATE NEW TOOL (Strict Last Resort):
{"action": "MAKE_TOOL", "tool_name": "short_snake_case_name", "description": "Specification of what the script must do", "args": {}}
CRITICAL: NEVER create a new tool for tasks that can be solved by running 'desktop_navigator', 'sys_exec', or existing tools!

4. TASK CONTINUATIONS & CONFIRMATIONS:
If the conversation history shows an active automated task (such as inspecting a webpage, playing music, or follow-up confirmations like 'done', 'try now', 'go on', 'check'):
CRITICAL: NEVER return 'DIRECT'! You MUST emit 'USE_TOOL' (e.g. 'desktop_navigator', 'take_screenshot', 'browser_agent', 'sys_exec') to execute!

5. DIRECT CONVERSATION (Only when truly purely conversational):
{"action": "DIRECT"}
Use ONLY for pure chit-chat, greetings, or conceptual questions where NO system, web, or pending task exists.

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
8. NO HALLUCINATED TASK COMPLETION: NEVER claim a song/video is playing, a window is closed/minimized, or a task is finished unless the tool actually executed that action successfully. Always report exact execution status truthfully.
9. TONE: Sharp, technical, concise, helpful, and proactive."""

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
8. NO HALLUCINATIONS: NEVER claim a song or video is playing on the user's screen unless desktop_navigator actually clicked it. Report real tool actions honestly."""

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
                if not tool_trace:
                    planner_prompt = (
                        f"{user_profile_context}\n\n"
                        f"Conversation History:\n{history_context}\n\n"
                        f"Available Tools:\n{json.dumps(manifest)}\n\n"
                        f"User Request: {prompt}"
                    )
                else:
                    steps_summary = "\n".join([
                        f"- Step {i+1}: Ran '{st['tool']}' with args {st['args']} -> Output:\n{st['output'][:1200]}"
                        for i, st in enumerate(tool_trace)
                    ])
                    planner_prompt = (
                        f"{user_profile_context}\n\n"
                        f"Conversation History:\n{history_context}\n\n"
                        f"Available Tools:\n{json.dumps(manifest)}\n\n"
                        f"Steps already completed in this turn:\n{steps_summary}\n\n"
                        f"Original User Request: {prompt}\n\n"
                        f"Based on completed steps and output above, decide the next action: USE_TOOL, FINISH, or MAKE_TOOL:"
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
                f"{user_profile_context}\n\nUser asked: {prompt}\n\nExecuted tool results across steps:\n{steps_context if steps_context else '(No tools were executed)'}\n\nSynthesize a clear, structured, and complete final answer for the user.",
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