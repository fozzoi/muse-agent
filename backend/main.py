import re
import os
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from core.config import Config
from core.router import generate
from core.database import load_local_manifest, save_local_manifest, sync_tools_from_cloud
from core.sandbox import run_tool, create_and_run_tool

# In-memory session chat history: list of {"role": "user"|"assistant", "content": "..."}
CHAT_HISTORY: list[dict] = []

ROUTER_SYSTEM_PROMPT = """You are the Orchestrator for a self-extending AI agent.
Analyze the user request, the recent conversation history, and the list of existing tools.
Return ONLY a valid JSON object matching one of these 3 actions:

1. If an existing tool in the list can solve it:
{"action": "USE_TOOL", "tool_name": "exact_existing_name", "args": {"key": "value"}}

2. If it requires system execution, file processing, web fetching, math/data manipulation, or automation and NO existing tool fits:
{"action": "MAKE_TOOL", "tool_name": "short_snake_case_name", "description": "Clear specification of what the Python script must do and print", "args": {"key": "value"}}

3. If it is general conversation, follow-up clarification, or standard Q&A:
{"action": "DIRECT"}

Note: Always format 'args' as a JSON key-value dictionary. Return raw JSON only."""

def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return {"action": "DIRECT"}

def _format_history_context() -> str:
    recent = CHAT_HISTORY[-6:]
    if not recent:
        return "None"
    return "\n".join([f"{msg['role'].upper()}: {msg['content']}" for msg in recent])

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

@app.post("/api/chat/clear")
def clear_history():
    global CHAT_HISTORY
    CHAT_HISTORY = []
    return {"status": "cleared"}

class ChatRequest(BaseModel):
    prompt: str = ""
    image: str | None = None
    mime_type: str | None = "image/jpeg"

async def _stream_chat(prompt: str, image: str | None = None, mime_type: str = "image/jpeg"):
    async def event_generator():
        global CHAT_HISTORY
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
                )
                CHAT_HISTORY.append({"role": "user", "content": f"[Image attached] {vision_prompt}"})
                CHAT_HISTORY.append({"role": "assistant", "content": final["text"]})
                yield {
                    "event": "result",
                    "data": json.dumps({
                        "text": final["text"],
                        "model": f"{final['provider']}/{final['model']}",
                    }),
                }
                return

            # 2. Standard text mode: triage & tool synthesis
            manifest = load_local_manifest()
            history_context = _format_history_context()

            yield {"event": "status", "data": json.dumps({"step": "Routing request...", "tools_count": len(manifest)})}

            # Triage via Router AI with conversation context
            router_prompt = (
                f"Conversation History:\n{history_context}\n\n"
                f"Existing tools:\n{json.dumps(manifest)}\n\n"
                f"Current user request: {prompt}"
            )
            route_res = await generate("router", router_prompt, system=ROUTER_SYSTEM_PROMPT)
            decision = _parse_json(route_res["text"])
            action = decision.get("action", "DIRECT")
            tool_args = decision.get("args", {})

            yield {
                "event": "status",
                "data": json.dumps({
                    "step": f"Decision: {action}",
                    "router_model": f"{route_res['provider']}/{route_res['model']}",
                    "decision": decision,
                }),
            }

            # Execute Existing Tool
            if action == "USE_TOOL":
                t_name = decision.get("tool_name", "")
                yield {"event": "status", "data": json.dumps({"step": f"Running tool '{t_name}'..."})}
                success, output = run_tool(t_name, tool_args)

                if success:
                    final = await generate(
                        "chat",
                        f"Conversation History:\n{history_context}\n\n"
                        f"User asked: {prompt}\nTool '{t_name}' returned:\n{output}\n"
                        f"Summarize the result clearly.",
                    )
                    CHAT_HISTORY.append({"role": "user", "content": prompt})
                    CHAT_HISTORY.append({"role": "assistant", "content": final["text"]})
                    yield {"event": "result", "data": json.dumps({"text": final["text"], "raw_tool_output": output, "model": f"{final['provider']}/{final['model']}"})}
                    return
                else:
                    yield {"event": "status", "data": json.dumps({"step": f"Tool '{t_name}' failed, regenerating tool..."})}
                    action = "MAKE_TOOL"
                    decision["description"] = f"Fix tool '{t_name}' to fulfill: {prompt}"

            # Synthesize & Debug New Tool
            if action == "MAKE_TOOL":
                t_name = decision.get("tool_name", "custom_tool")
                t_desc = decision.get("description", prompt)
                yield {"event": "status", "data": json.dumps({"step": f"Synthesizing new tool '{t_name}'..."})}

                build_res = await create_and_run_tool(t_name, t_desc, tool_args)
                if build_res["success"]:
                    yield {"event": "status", "data": json.dumps({"step": f"Tool '{t_name}' created. Formatting answer..."})}
                    final = await generate(
                        "chat",
                        f"Conversation History:\n{history_context}\n\n"
                        f"User asked: {prompt}\nTool '{t_name}' produced:\n{build_res['output']}\n"
                        f"Provide the final response.",
                    )
                    CHAT_HISTORY.append({"role": "user", "content": prompt})
                    CHAT_HISTORY.append({"role": "assistant", "content": final["text"]})
                    yield {
                        "event": "result",
                        "data": json.dumps({
                            "text": final["text"],
                            "raw_tool_output": build_res["output"],
                            "tool_created": t_name,
                            "model": f"{final['provider']}/{final['model']}",
                        }),
                    }
                else:
                    yield {"event": "error", "data": json.dumps({"error": build_res["output"]})}
                return

            # Direct Response
            chat_prompt = f"Conversation History:\n{history_context}\n\nUser: {prompt}"
            ans = await generate("chat", chat_prompt)
            CHAT_HISTORY.append({"role": "user", "content": prompt})
            CHAT_HISTORY.append({"role": "assistant", "content": ans["text"]})
            yield {"event": "result", "data": json.dumps({"text": ans["text"], "model": f"{ans['provider']}/{ans['model']}"})}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())

@app.get("/api/chat/stream")
async def chat_stream_get(prompt: str):
    return await _stream_chat(prompt)

@app.post("/api/chat/stream")
async def chat_stream_post(req: ChatRequest):
    return await _stream_chat(req.prompt, req.image, req.mime_type or "image/jpeg")