import os
import json
import base64
import asyncio
from telegram import Update
import telegram.error
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

from core.config import Config
from core.router import generate
from core.database import load_local_manifest
from core.sandbox import run_tool, create_and_run_tool
from core.memory import add_message, format_history_context, clear_session, get_history, save_upload_image
from main import ROUTER_SYSTEM_PROMPT, SYNTHESIS_SYSTEM_PROMPT, _parse_json

IMAGE_FILTER = (
    filters.PHOTO
    | filters.Document.IMAGE
    | filters.Document.FileExtension("png")
    | filters.Document.FileExtension("jpg")
    | filters.Document.FileExtension("jpeg")
    | filters.Document.FileExtension("webp")
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"[Telegram Bot] /start from {update.effective_chat.id}", flush=True)
    await update.message.reply_text("Muse Agent is online. Send me any task, question, or send a photo for AI vision analysis! Send /clear to reset memory.")

async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    print(f"[Telegram Bot] /clear from {chat_id}", flush=True)
    clear_session(f"tg_{chat_id}")
    await update.message.reply_text("🧹 Conversation memory cleared!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = str(update.effective_chat.id)
    session_id = f"tg_{chat_id}"
    user_prompt = update.message.text
    print(f"[Telegram Bot] Received text from {chat_id}: {user_prompt[:80]}", flush=True)
    status_msg = await update.message.reply_text("🤔 Routing request...")

    try:
        manifest = load_local_manifest()
        history_context = format_history_context(session_id)

        # 1. Triage via Router AI with conversation context
        router_prompt = (
            f"Conversation History:\n{history_context}\n\n"
            f"Existing tools:\n{json.dumps(manifest)}\n\n"
            f"Current user request: {user_prompt}"
        )
        route_res = await generate("router", router_prompt, system=ROUTER_SYSTEM_PROMPT)
        decision = _parse_json(route_res["text"])
        action = decision.get("action", "DIRECT")
        tool_args = decision.get("args", {})

        # 2. Existing Tool
        if action == "USE_TOOL":
            t_name = decision.get("tool_name", "")
            await status_msg.edit_text(f"⚙️ Running existing tool: `{t_name}`...", parse_mode="Markdown")
            success, output = run_tool(t_name, tool_args)

            if success:
                final = await generate(
                    "chat",
                    f"Tool '{t_name}' returned:\n{output}\nSummarize clearly.",
                    system=SYNTHESIS_SYSTEM_PROMPT,
                    history=get_history(session_id, limit=10),
                )
                add_message(session_id, "user", user_prompt)
                add_message(session_id, "assistant", final["text"])
                reply = final["text"]
                if len(reply) > 4000:
                    chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
                    await status_msg.edit_text(chunks[0])
                    for ch in chunks[1:]:
                        await update.message.reply_text(ch)
                else:
                    await status_msg.edit_text(reply)
                return
            else:
                action = "MAKE_TOOL"
                decision["description"] = f"Fix tool '{t_name}' to fulfill: {user_prompt}"

        # 3. Dynamic Tool Maker
        if action == "MAKE_TOOL":
            t_name = decision.get("tool_name", "custom_tool")
            t_desc = decision.get("description", user_prompt)
            await status_msg.edit_text(f"🛠️ Synthesizing new tool `{t_name}`...", parse_mode="Markdown")

            build_res = await create_and_run_tool(t_name, t_desc, tool_args)
            if build_res["success"]:
                await status_msg.edit_text(" Formatting final response...")
                final = await generate(
                    "chat",
                    f"Newly created tool '{t_name}' produced:\n{build_res['output']}\nProvide final answer.",
                    system=SYNTHESIS_SYSTEM_PROMPT,
                    history=get_history(session_id, limit=10),
                )
                add_message(session_id, "user", user_prompt)
                add_message(session_id, "assistant", final["text"])
                reply_text = f"{final['text']}\n\n*(Created tool: `{t_name}.py`)*"
                if len(reply_text) > 4000:
                    chunks = [reply_text[i:i+4000] for i in range(0, len(reply_text), 4000)]
                    await status_msg.edit_text(chunks[0], parse_mode="Markdown")
                    for ch in chunks[1:]:
                        await update.message.reply_text(ch)
                else:
                    await status_msg.edit_text(reply_text, parse_mode="Markdown")
            else:
                await status_msg.edit_text(f"❌ Tool build failed:\n`{build_res['output']}`", parse_mode="Markdown")
            return

        # 4. Direct Response with memory
        ans = await generate("chat", user_prompt, system=SYNTHESIS_SYSTEM_PROMPT, history=get_history(session_id, limit=10))
        add_message(session_id, "user", user_prompt)
        add_message(session_id, "assistant", ans["text"])
        reply = ans["text"]
        if len(reply) > 4000:
            chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
            await status_msg.edit_text(chunks[0])
            for ch in chunks[1:]:
                await update.message.reply_text(ch)
        else:
            await status_msg.edit_text(reply)

    except Exception as e:
        print(f"[Telegram Bot] Message error: {e}", flush=True)
        try:
            await status_msg.edit_text(f"⚠️ Error: {str(e)}")
        except Exception:
            await update.message.reply_text(f"⚠️ Error: {str(e)}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = str(update.effective_chat.id)
    session_id = f"tg_{chat_id}"
    caption = update.message.caption or "Analyze this image and describe what you see in detail."

    photo_file = None
    mime_type = "image/jpeg"

    if update.message.photo:
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        mime_type = "image/jpeg"
    elif update.message.document:
        doc = update.message.document
        doc_mime = doc.mime_type or ""
        doc_name = (doc.file_name or "").lower()
        if doc_mime.startswith("image/") or doc_name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
            photo_file = await doc.get_file()
            mime_type = doc_mime if doc_mime.startswith("image/") else "image/jpeg"
        else:
            return
    else:
        return

    print(f"[Telegram Bot] Processing vision request from chat {chat_id} (mime: {mime_type}, caption: {caption[:50]!r})", flush=True)
    status_msg = await update.message.reply_text("🔍 Analyzing image with Muse Vision...")

    try:
        photo_bytes = await photo_file.download_as_bytearray()
        b64_image = base64.b64encode(photo_bytes).decode("utf-8")

        res = await generate(
            "vision",
            prompt=caption,
            system="You are an expert AI vision analyst. Inspect the image closely and provide clear, accurate, and structured observations.",
            image_data=b64_image,
            mime_type=mime_type,
            history=get_history(session_id, limit=6),
        )

        image_url = save_upload_image(b64_image, mime_type)
        add_message(session_id, "user", caption, extra={"image": image_url} if image_url else None)
        add_message(session_id, "assistant", res["text"])

        reply = f"{res['text']}\n\n*(Analyzed via `{res['provider']}/{res['model']}`)*"
        if len(reply) > 4000:
            chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
            await status_msg.edit_text(chunks[0])
            for ch in chunks[1:]:
                await update.message.reply_text(ch)
        else:
            await status_msg.edit_text(reply)
    except Exception as e:
        print(f"[Telegram Bot] Vision analysis error: {e}", flush=True)
        try:
            await status_msg.edit_text(f"⚠️ Vision analysis error: {str(e)}")
        except Exception:
            await update.message.reply_text(f"⚠️ Vision analysis error: {str(e)}")

def main():
    token = Config.TELEGRAM_BOT_TOKEN
    if not token:
        print("[Telegram Bot] TELEGRAM_BOT_TOKEN not configured in .env. Exiting.", flush=True)
        return

    # Python 3.14 requirement: ensure an event loop is set for the main thread
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, telegram.error.NetworkError):
            print(f"[Telegram Bot] Network glitch: {context.error}", flush=True)
            return
        print(f"[Telegram Bot] Warning: {context.error}", flush=True)

    request = HTTPXRequest(connect_timeout=15.0, read_timeout=30.0)
    app = ApplicationBuilder().token(token).request(request).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear_chat))
    app.add_handler(MessageHandler(IMAGE_FILTER, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("[Telegram Bot] Bot is polling for messages...", flush=True)
    app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)

if __name__ == "__main__":
    main()