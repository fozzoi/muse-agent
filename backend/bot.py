import os
import re
import html
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
from main import ROUTER_SYSTEM_PROMPT, SYNTHESIS_SYSTEM_PROMPT, TELEGRAM_SYNTHESIS_SYSTEM_PROMPT, _parse_json

IMAGE_FILTER = (
    filters.PHOTO
    | filters.Document.IMAGE
    | filters.Document.FileExtension("png")
    | filters.Document.FileExtension("jpg")
    | filters.Document.FileExtension("jpeg")
    | filters.Document.FileExtension("webp")
)

async def is_authorized(update: Update) -> bool:
    """Verifies if the sending Telegram user is authorized to interact with Muse."""
    allowed = Config.TELEGRAM_ALLOWED_USERS
    if not allowed:
        return True

    user = update.effective_user
    if not user:
        return False

    user_id = str(user.id).strip().lower()
    username = (user.username or "").strip().lower().lstrip("@")

    if user_id in allowed or username in allowed:
        return True

    u_display = f"@{user.username}" if user.username else f"User {user.id}"
    print(f"[Telegram Bot] ⛔ Unauthorized access denied for {u_display} (ID: {user.id})", flush=True)
    if update.message:
        await update.message.reply_text(
            f"⛔ *Access Denied*\n\n"
            f"Your Telegram User ID is: `{user.id}`\n\n"
            f"For security, Muse Agent has direct local execution access on the host Linux system and is restricted to authorized users.\n\n"
            f"To authorize this account, add `{user.id}` to `TELEGRAM_ALLOWED_USERS` in your backend `.env` file.",
            parse_mode="Markdown",
        )
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return
    print(f"[Telegram Bot] /start from {update.effective_chat.id}", flush=True)
    await update.message.reply_text("Muse Agent is online. Send me any task, question, or send a photo for AI vision analysis! Send /clear to reset memory.")

async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    print(f"[Telegram Bot] /clear from {chat_id}", flush=True)
    clear_session(f"tg_{chat_id}")
    await update.message.reply_text("🧹 Conversation memory cleared!")

def extract_image_from_tool_output(output: str) -> str | None:
    if not output:
        return None
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            for k in ("file_path", "image_path", "path", "file", "dest_file"):
                val = data.get(k)
                if val and isinstance(val, str):
                    exp = os.path.expanduser(val.strip())
                    if os.path.isfile(exp) and exp.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        return exp
    except Exception:
        pass

    matches = re.findall(r'(?:[\'"]|(?<=\s)|^)((?:/[\w\.\-]+)+/(?:[\w\.\-]+\.(?:png|jpg|jpeg|webp)))', output, re.IGNORECASE)
    for m in matches:
        exp = os.path.expanduser(m.strip())
        if os.path.isfile(exp):
            return exp

    matches_home = re.findall(r'(?:[\'"]|(?<=\s)|^)(~/(?:[\w\.\-]+/)*(?:[\w\.\-]+\.(?:png|jpg|jpeg|webp)))', output, re.IGNORECASE)
    for m in matches_home:
        exp = os.path.expanduser(m.strip())
        if os.path.isfile(exp):
            return exp

    return None

def markdown_to_telegram_html(md: str) -> str:
    """Safely converts Markdown into Telegram-compatible HTML, converting tables and headers for mobile."""
    if not md:
        return ""

    # 1. Protect multi-line code blocks
    code_blocks = []
    def save_code_block(match):
        code = match.group(2)
        escaped_code = html.escape(code.strip("\n"))
        idx = len(code_blocks)
        code_blocks.append(f"<pre><code>{escaped_code}</code></pre>")
        return f"__CODE_BLOCK_{idx}__"

    text = re.sub(r"```([a-zA-Z0-9_\-\+]*)\n(.*?)```", save_code_block, md, flags=re.DOTALL)

    # 2. Protect inline code
    inline_codes = []
    def save_inline(match):
        c = match.group(1)
        idx = len(inline_codes)
        inline_codes.append(f"<code>{html.escape(c)}</code>")
        return f"__INLINE_CODE_{idx}__"

    text = re.sub(r"`([^`\n]+)`", save_inline, text)

    # 3. HTML escape remaining text
    text = html.escape(text)

    # 4. Convert markdown tables into clean bulleted list for mobile
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            cleaned_lines.append("• " + " — ".join(c for c in cells if c))
        else:
            cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # 5. Convert Headers (#, ##, ###) into bold
    text = re.sub(r"^(?:#{1,6})\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 6. Convert Bold (**text**)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # 7. Convert Italic (*text*)
    text = re.sub(r"(?<!\w)\*([^\*\n]+)\*(?!\w)", r"<i>\1</i>", text)

    # 8. Restore code blocks & inline code
    for i, cb in enumerate(code_blocks):
        text = text.replace(f"__CODE_BLOCK_{i}__", cb)
    for i, ic in enumerate(inline_codes):
        text = text.replace(f"__INLINE_CODE_{i}__", ic)

    return text

async def deliver_response(update: Update, status_msg, reply_text: str, image_file: str | None = None):
    html_text = markdown_to_telegram_html(reply_text)

    if image_file and os.path.isfile(image_file):
        print(f"[Telegram Bot] Delivering image to chat: {image_file}", flush=True)
        caption_html = html_text[:1024] if html_text else None
        caption_plain = reply_text[:1024] if reply_text else None
        sent_photo = False
        try:
            with open(image_file, "rb") as photo_f:
                await update.message.reply_photo(photo=photo_f, caption=caption_html, parse_mode="HTML")
            sent_photo = True
        except Exception:
            try:
                with open(image_file, "rb") as photo_f:
                    await update.message.reply_photo(photo=photo_f, caption=caption_plain)
                sent_photo = True
            except Exception as e:
                print(f"[Telegram Bot] Failed to send photo: {e}", flush=True)

        if sent_photo:
            try:
                await status_msg.delete()
            except Exception:
                pass
            if len(html_text) > 1024:
                rem_html = html_text[1024:]
                rem_plain = reply_text[1024:]
                for i in range(0, len(rem_html), 4000):
                    try:
                        await update.message.reply_text(rem_html[i:i+4000], parse_mode="HTML")
                    except Exception:
                        await update.message.reply_text(rem_plain[i:i+4000])
            return

    # Text delivery with HTML parsing and plain-text fallback
    if len(html_text) > 4000:
        chunks_html = [html_text[i:i+4000] for i in range(0, len(html_text), 4000)]
        chunks_plain = [reply_text[i:i+4000] for i in range(0, len(reply_text), 4000)]
        try:
            await status_msg.edit_text(chunks_html[0], parse_mode="HTML")
        except Exception:
            await status_msg.edit_text(chunks_plain[0])

        for ch_h, ch_p in zip(chunks_html[1:], chunks_plain[1:]):
            try:
                await update.message.reply_text(ch_h, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(ch_p)
    else:
        try:
            await status_msg.edit_text(html_text, parse_mode="HTML")
        except Exception:
            await status_msg.edit_text(reply_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    if not await is_authorized(update):
        return

    chat_id = str(update.effective_chat.id)
    session_id = f"tg_{chat_id}"
    user_prompt = update.message.text
    print(f"[Telegram Bot] Received text from {chat_id}: {user_prompt[:80]}", flush=True)
    status_msg = await update.message.reply_text("🤔 Routing request...")

    try:
        manifest = load_local_manifest()
        history_context = format_history_context(session_id)

        max_steps = 4
        step_count = 0
        tool_trace = []
        combined_raw = []
        images_collected = []
        created_tool_name = None

        while step_count < max_steps:
            step_count += 1
            if not tool_trace:
                planner_prompt = (
                    f"Conversation History:\n{history_context}\n\n"
                    f"Available Tools:\n{json.dumps(manifest)}\n\n"
                    f"Current user request: {user_prompt}"
                )
            else:
                steps_summary = "\n".join([
                    f"- Step {i+1}: Ran '{st['tool']}' with args {st['args']} -> Output:\n{st['output'][:1200]}"
                    for i, st in enumerate(tool_trace)
                ])
                planner_prompt = (
                    f"Conversation History:\n{history_context}\n\n"
                    f"Available Tools:\n{json.dumps(manifest)}\n\n"
                    f"Steps already completed in this turn:\n{steps_summary}\n\n"
                    f"Original User Request: {user_prompt}\n\n"
                    f"Based on completed steps and output above, decide the next action: USE_TOOL, FINISH, or MAKE_TOOL:"
                )

            route_res = await generate("router", planner_prompt, system=ROUTER_SYSTEM_PROMPT)
            decision = _parse_json(route_res["text"])
            action = decision.get("action", "FINISH" if tool_trace else "DIRECT")

            if action == "FINISH":
                break

            if action == "DIRECT":
                if not tool_trace:
                    ans = await generate("chat", user_prompt, system=TELEGRAM_SYNTHESIS_SYSTEM_PROMPT, history=get_history(session_id, limit=10))
                    add_message(session_id, "user", user_prompt)
                    add_message(session_id, "assistant", ans["text"])
                    await deliver_response(update, status_msg, ans["text"])
                    return
                else:
                    break

            if action == "USE_TOOL":
                t_name = decision.get("tool_name", "")
                tool_args = decision.get("args", {})
                try:
                    await status_msg.edit_text(f"⚙️ Step {step_count}: Running `{t_name}`...", parse_mode="Markdown")
                except Exception:
                    pass

                success, output = run_tool(t_name, tool_args)
                if success:
                    tool_trace.append({"tool": t_name, "args": tool_args, "output": output})
                    combined_raw.append(f"[{t_name}]:\n{output}")
                    img = extract_image_from_tool_output(output)
                    if img and img not in images_collected:
                        images_collected.append(img)
                    continue
                else:
                    try:
                        await status_msg.edit_text(f"⚠️ Step {step_count}: `{t_name}` failed, adapting...", parse_mode="Markdown")
                    except Exception:
                        pass
                    action = "MAKE_TOOL"
                    decision["description"] = f"Fix tool '{t_name}' to fulfill: {user_prompt}"
                    decision["tool_name"] = f"fix_{t_name}"

            if action == "MAKE_TOOL":
                t_name = decision.get("tool_name", "custom_tool")
                t_desc = decision.get("description", user_prompt)
                tool_args = decision.get("args", {})
                try:
                    await status_msg.edit_text(f"🛠️ Synthesizing new tool `{t_name}`...", parse_mode="Markdown")
                except Exception:
                    pass

                build_res = await create_and_run_tool(t_name, t_desc, tool_args)
                if build_res["success"]:
                    created_tool_name = t_name
                    tool_trace.append({"tool": t_name, "args": tool_args, "output": build_res["output"]})
                    combined_raw.append(f"[{t_name}]:\n{build_res['output']}")
                    img = extract_image_from_tool_output(build_res["output"])
                    if img and img not in images_collected:
                        images_collected.append(img)
                    break
                else:
                    try:
                        await status_msg.edit_text(f"❌ Tool build failed:\n`{build_res['output']}`", parse_mode="Markdown")
                    except Exception:
                        pass
                    return

        # Synthesize final multi-tool response for Telegram
        try:
            await status_msg.edit_text("✨ Formatting final response...")
        except Exception:
            pass

        steps_context = "\n\n---\n\n".join(combined_raw)
        final = await generate(
            "chat",
            f"User asked: {user_prompt}\n\nExecuted tool results across steps:\n{steps_context}\n\nProvide the final structured answer for the user on Telegram.",
            system=TELEGRAM_SYNTHESIS_SYSTEM_PROMPT,
            history=get_history(session_id, limit=10),
        )
        reply_text = final["text"]
        if created_tool_name:
            reply_text += f"\n\n*(Created tool: `{created_tool_name}.py`)*"

        add_message(session_id, "user", user_prompt)
        add_message(session_id, "assistant", reply_text)

        img_to_send = images_collected[-1] if images_collected else None
        await deliver_response(update, status_msg, reply_text, img_to_send)

    except Exception as e:
        print(f"[Telegram Bot] Message error: {e}", flush=True)
        try:
            await status_msg.edit_text(f"⚠️ Error: {str(e)}")
        except Exception:
            await update.message.reply_text(f"⚠️ Error: {str(e)}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not await is_authorized(update):
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
            system="You are Muse Vision, an expert visual analyst chatting on Telegram mobile. Provide clear, concise observations using emoji bullet points. Do not use Markdown tables.",
            image_data=b64_image,
            mime_type=mime_type,
            history=get_history(session_id, limit=6),
        )

        image_url = save_upload_image(b64_image, mime_type)
        add_message(session_id, "user", caption, extra={"image": image_url} if image_url else None)
        add_message(session_id, "assistant", res["text"])

        reply = f"{res['text']}\n\n*(Analyzed via `{res['provider']}/{res['model']}`)*"
        await deliver_response(update, status_msg, reply)
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

    request = HTTPXRequest(connect_timeout=30.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0)
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