import os
import sys
import re
import html
import json
import base64
import fcntl
import asyncio
import httpx
from telegram import Update
import telegram.error
from telegram.request import HTTPXRequest
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

from core.config import Config
from core.router import generate
from core.database import load_local_manifest
from core.sandbox import run_tool, create_and_run_tool
from core.memory import add_message, format_history_context, clear_session, get_history, save_upload_image
from core.watchdog import watchdog
from core.user_profile import get_user_profile_context, format_profile_for_telegram, add_learned_rule, add_memory
from core.evolution import reflect_and_evolve
from core.quota_manager import get_all_cooldowns
from main import ROUTER_SYSTEM_PROMPT, SYNTHESIS_SYSTEM_PROMPT, TELEGRAM_SYNTHESIS_SYSTEM_PROMPT, _parse_json

# Module-level variable to hold file handle so it's not garbage-collected
_LOCK_FILE = None

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

async def safe_edit_text(msg, text: str, parse_mode: str | None = None) -> bool:
    """Safely edits message text, ignoring 'Message is not modified' and network glitches."""
    if not msg:
        return False
    try:
        await msg.edit_text(text, parse_mode=parse_mode)
        return True
    except telegram.error.BadRequest as e:
        if "Message is not modified" in str(e):
            return True
        print(f"[Telegram Bot] Warning updating status message: {e}", flush=True)
    except Exception as e:
        print(f"[Telegram Bot] Transient edit error: {e}", flush=True)
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return
    print(f"[Telegram Bot] /start from {update.effective_chat.id}", flush=True)
    await update.message.reply_text(
        "👋 Muse Agent is online!\n\n"
        "• Send tasks, questions, or voice notes\n"
        "• Send screenshots or photos for vision analysis\n"
        "• Use /profile to see your evolved profile & learned rules\n"
        "• Use /learn <fact> to teach Muse preferences directly\n"
        "• Use /quota to inspect model rates & circuit breaker status\n"
        "• Use /login <url> to authenticate web sessions on desktop\n"
        "• Use /clear to reset conversation memory"
    )

async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    print(f"[Telegram Bot] /clear from {chat_id}", flush=True)
    clear_session(f"tg_{chat_id}")
    await update.message.reply_text("🧹 Conversation memory cleared! (Your permanent evolved profile & skills remain intact)")

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the user's evolved profile, location, preferences, and learned rules."""
    if not await is_authorized(update):
        return
    text = format_profile_for_telegram()
    await update.message.reply_text(text, parse_mode="Markdown")

async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows the user to explicitly teach Muse a preference, location, or rule."""
    if not await is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/learn <fact or rule>`\nExample: `/learn I prefer dark mode`", parse_mode="Markdown")
        return
    fact = " ".join(context.args).strip()
    add_learned_rule(fact)
    add_memory(fact)
    await update.message.reply_text(f"🧠 *Learned:* \"_{fact}_\"\n\nAdded to your persistent profile!", parse_mode="Markdown")

async def quota_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays which models are currently banned or cooling down due to 429 quota exhaustion."""
    if not await is_authorized(update):
        return
    cooldowns = get_all_cooldowns()
    if not cooldowns:
        await update.message.reply_text("✅ *All Models Operational*\n\nAll AI models across Gemini, Groq, and Mistral are active with zero quota cooldowns!", parse_mode="Markdown")
        return

    lines = ["⚡ *Active Model Quota & Circuit Breaker Status*", ""]
    for k, v in cooldowns.items():
        lines.append(f"• `{k}`: *{v['remaining_human']}* left\n  _({v['reason']})_")

    lines.append("\n👁️ *Active Vision Fallbacks:* `gemini-3.5-flash-lite`, `gemini-2.5-flash`, `pixtral-12b`")
    lines.append("⚡ *Active Coder Fallbacks:* `codestral-latest`, `gemini-2.5-flash`, `openai/gpt-oss-120b`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggers the desktop browser window to authenticate a service."""
    if not await is_authorized(update):
        return

    target = "https://www.instagram.com/accounts/login/"
    if context.args:
        target = context.args[0]
        if not target.startswith(("http://", "https://")):
            target = f"https://{target}"

    status_msg = await update.message.reply_text(
        f"🖥️ *Opening Desktop Login Window*\n\n"
        f"Target: `{target}`\n"
        f"Please switch to your screen to complete the login (including 2FA/Captchas).\n\n"
        f"⏳ Waiting for successful navigation (up to 3 minutes)...",
        parse_mode="Markdown",
    )

    # Offload the blocking Playwright execution to a thread
    loop = asyncio.get_running_loop()
    success, output = await loop.run_in_executor(
        None, run_tool, "browser_agent", {"action": "setup_login", "url": target}
    )

    try:
        data = json.loads(output)
        if data.get("status") == "success":
            await safe_edit_text(
                status_msg,
                f"✅ *Login Completed!*\n\n"
                f"Session cookies are permanently saved for `{target}`.\n"
                f"You can now run tasks headlessly from Telegram!",
                parse_mode="Markdown",
            )
        else:
            await safe_edit_text(
                status_msg,
                f"❌ *Login Session Failed or Timed Out*\n\n`{data.get('error', output)}`",
                parse_mode="Markdown",
            )
    except Exception:
        await safe_edit_text(status_msg, f"ℹ️ Result: {output}")

def extract_image_from_tool_output(output: str) -> str | None:
    if not output:
        return None
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            for k in ("file_path", "image_path", "path", "file", "dest_file", "screenshot"):
                val = data.get(k)
                if val and isinstance(val, str):
                    exp = os.path.expanduser(val.strip())
                    if os.path.isfile(exp) and exp.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        return exp

            out_dir = data.get("output_dir")
            if out_dir and isinstance(out_dir, str):
                exp_dir = os.path.expanduser(out_dir.strip())
                if os.path.isdir(exp_dir):
                    flist = data.get("files", [])
                    if isinstance(flist, list):
                        for fn in flist:
                            fp = os.path.join(exp_dir, str(fn))
                            if os.path.isfile(fp) and fp.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                                return fp
                    for fn in sorted(os.listdir(exp_dir)):
                        fp = os.path.join(exp_dir, fn)
                        if os.path.isfile(fp) and fp.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                            return fp
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
    """Safely converts Markdown into Telegram-compatible HTML."""
    if not md:
        return ""

    code_blocks = []
    def save_code_block(match):
        code = match.group(2)
        escaped_code = html.escape(code.strip("\n"))
        idx = len(code_blocks)
        code_blocks.append(f"<pre><code>{escaped_code}</code></pre>")
        return f"__CODE_BLOCK_{idx}__"

    text = re.sub(r"```([a-zA-Z0-9_\-\+]*)\n(.*?)```", save_code_block, md, flags=re.DOTALL)

    inline_codes = []
    def save_inline(match):
        c = match.group(1)
        idx = len(inline_codes)
        inline_codes.append(f"<code>{html.escape(c)}</code>")
        return f"__INLINE_CODE_{idx}__"

    text = re.sub(r"`([^`\n]+)`", save_inline, text)

    # Protect Markdown Links [label](url)
    links = []
    def save_link(match):
        label = html.escape(match.group(1))
        url = html.escape(match.group(2))
        idx = len(links)
        links.append(f'<a href="{url}">{label}</a>')
        return f"__LINK_BLOCK_{idx}__"

    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", save_link, text)

    text = html.escape(text)

    # Convert tables to bullet points for mobile viewports
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

    text = re.sub(r"^(?:#{1,6})\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)\*([^\*\n]+)\*(?!\w)", r"<i>\1</i>", text)

    for i, cb in enumerate(code_blocks):
        text = text.replace(f"__CODE_BLOCK_{i}__", cb)
    for i, ic in enumerate(inline_codes):
        text = text.replace(f"__INLINE_CODE_{i}__", ic)
    for i, lk in enumerate(links):
        text = text.replace(f"__LINK_BLOCK_{i}__", lk)

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
        user_profile_context = get_user_profile_context()

        max_steps = 4
        step_count = 0
        tool_trace = []
        combined_raw = []
        images_collected = []
        created_tool_name = None
        created_tool_info = None

        while step_count < max_steps:
            step_count += 1
            if not tool_trace:
                planner_prompt = (
                    f"{user_profile_context}\n\n"
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
                    f"{user_profile_context}\n\n"
                    f"Conversation History:\n{history_context}\n\n"
                    f"Available Tools:\n{json.dumps(manifest)}\n\n"
                    f"Steps already completed in this turn:\n{steps_summary}\n\n"
                    f"Original User Request: {user_prompt}\n\n"
                    f"Based on completed steps and output above, decide the next action: USE_TOOL, FINISH, or MAKE_TOOL:"
                )

            route_res = await generate("router", planner_prompt, system=ROUTER_SYSTEM_PROMPT)
            decision = _parse_json(route_res["text"])
            action = decision.get("action", "FINISH" if tool_trace else "DIRECT")
            print(f"[Telegram Bot] Step {step_count}: Action={action}, Tool={decision.get('tool_name')}, Args={decision.get('args')}", flush=True)

            if action == "FINISH":
                break

            if action == "DIRECT":
                if not tool_trace:
                    direct_sys = f"{TELEGRAM_SYNTHESIS_SYSTEM_PROMPT}\n\n{user_profile_context}\n\nYour active local tools in tools/manifest.json:\n{json.dumps(manifest, indent=2)}"
                    ans = await generate("chat", user_prompt, system=direct_sys, history=get_history(session_id, limit=10))
                    add_message(session_id, "user", user_prompt)
                    add_message(session_id, "assistant", ans["text"])
                    await deliver_response(update, status_msg, ans["text"])
                    return
                else:
                    break

            if action == "USE_TOOL":
                t_name = decision.get("tool_name", "")
                tool_args = decision.get("args", {})
                if not t_name:
                    print(f"[Telegram Bot] Step {step_count}: USE_TOOL with missing tool_name! Breaking.", flush=True)
                    break
                await safe_edit_text(status_msg, f"⚙️ Step {step_count}: Running `{t_name}`...", parse_mode="Markdown")

                print(f"[Telegram Bot] Step {step_count}: Running '{t_name}' with {tool_args}", flush=True)
                success, output = run_tool(t_name, tool_args)
                print(f"[Telegram Bot] Step {step_count}: Tool '{t_name}' success={success} (output {len(output)} chars)", flush=True)

                # Check if tool indicates authentication is missing
                try:
                    p_out = json.loads(output)
                    if isinstance(p_out, dict) and p_out.get("status") == "LOGIN_REQUIRED":
                        await safe_edit_text(
                            status_msg,
                            f"🔒 *Authentication Required*\n\n"
                            f"{p_out.get('notice', 'Please authenticate your session.')}\n\n"
                            f"Send `/login <service_url>` to open a login window on your desktop.",
                            parse_mode="Markdown",
                        )
                        return
                except Exception:
                    pass

                tool_trace.append({"tool": t_name, "args": tool_args, "output": output if success else f"TOOL ERROR: {output}"})
                combined_raw.append(f"[{t_name}]:\n{output}")
                if success:
                    img = extract_image_from_tool_output(output)
                    if img and img not in images_collected:
                        images_collected.append(img)
                else:
                    await safe_edit_text(status_msg, f"⚠️ Step {step_count}: `{t_name}` error, re-planning...", parse_mode="Markdown")
                continue

            if action == "MAKE_TOOL":
                t_name = decision.get("tool_name", "custom_tool")
                t_desc = decision.get("description", user_prompt)
                tool_args = decision.get("args", {})
                await safe_edit_text(status_msg, f"🛠️ Synthesizing new tool `{t_name}`...", parse_mode="Markdown")

                print(f"[Telegram Bot] Step {step_count}: Synthesizing '{t_name}': {t_desc[:80]}", flush=True)
                build_res = await create_and_run_tool(t_name, t_desc, tool_args)
                print(f"[Telegram Bot] Step {step_count}: Synthesis result success={build_res.get('success')}", flush=True)
                if build_res["success"]:
                    created_tool_name = t_name
                    created_tool_info = build_res
                    tool_trace.append({"tool": t_name, "args": tool_args, "output": build_res["output"]})
                    combined_raw.append(f"[{t_name}]:\n{build_res['output']}")
                    img = extract_image_from_tool_output(build_res["output"])
                    if img and img not in images_collected:
                        images_collected.append(img)
                    break
                else:
                    await safe_edit_text(status_msg, f"❌ Tool build failed:\n`{build_res['output']}`", parse_mode="Markdown")
                    return

        await safe_edit_text(status_msg, "✨ Formatting final response...")

        steps_context = "\n\n---\n\n".join(combined_raw)
        synthesis_prompt = (
            f"{user_profile_context}\n\n"
            f"User asked: {user_prompt}\n\n"
            f"Executed tool results across steps:\n{steps_context if steps_context else '(No tools were executed)'}\n\n"
            f"Provide the final structured answer for the user on Telegram."
        )
        final = await generate(
            "chat",
            synthesis_prompt,
            system=TELEGRAM_SYNTHESIS_SYSTEM_PROMPT,
            history=get_history(session_id, limit=10),
        )
        reply_text = final["text"]
        if created_tool_name:
            if created_tool_info and created_tool_info.get("is_package"):
                reply_text += f"\n\n*(Created modular tool package: `tools/{created_tool_name}/` with {created_tool_info.get('files_count', 1)} files)*"
            else:
                reply_text += f"\n\n*(Created tool: `{created_tool_name}.py`)*"

        add_message(session_id, "user", user_prompt)
        add_message(session_id, "assistant", reply_text)

        img_to_send = images_collected[-1] if images_collected else None
        print(f"[Telegram Bot] Delivering final response ({len(reply_text)} chars, image={img_to_send}) to {chat_id}", flush=True)
        await deliver_response(update, status_msg, reply_text, img_to_send)

        # Hermes autonomous evolution: reflect on this turn in the background
        asyncio.create_task(reflect_and_evolve(user_prompt, reply_text, tool_trace))

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

async def transcribe_voice(audio_bytes: bytearray, filename: str = "voice.ogg", mime_type: str = "audio/ogg") -> str:
    """Transcribes audio using Groq Whisper API in <1 second."""
    if not Config.GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is not configured for voice transcription.")
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {Config.GROQ_API_KEY}"}
    files = {"file": (filename, bytes(audio_bytes), mime_type)}
    data = {"model": "whisper-large-v3-turbo"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, files=files, data=data)
        resp.raise_for_status()
        return resp.json().get("text", "").strip()

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not (update.message.voice or update.message.audio):
        return
    if not await is_authorized(update):
        return

    chat_id = str(update.effective_chat.id)
    voice_obj = update.message.voice or update.message.audio
    mime = getattr(voice_obj, "mime_type", "audio/ogg") or "audio/ogg"
    ext = "ogg" if "ogg" in mime else ("mp3" if "mp3" in mime else "m4a")

    print(f"[Telegram Bot] Received voice note from {chat_id} (mime: {mime})", flush=True)
    status_msg = await update.message.reply_text("🎙️ Transcribing audio note with Whisper...")

    try:
        f = await voice_obj.get_file()
        audio_bytes = await f.download_as_bytearray()
        transcribed_text = await transcribe_voice(audio_bytes, filename=f"voice_{chat_id}.{ext}", mime_type=mime)
        if not transcribed_text:
            await status_msg.edit_text("⚠️ Could not detect speech in the audio note.")
            return

        print(f"[Telegram Bot] Transcribed voice: {transcribed_text!r}", flush=True)
        await safe_edit_text(status_msg, f"🗣️ *Transcribed:* \"_{transcribed_text}_\"\n\n🤔 Routing request...", parse_mode="Markdown")

        update.message.text = transcribed_text
        await handle_message(update, context)
    except Exception as e:
        print(f"[Telegram Bot] Voice note error: {e}", flush=True)
        try:
            await status_msg.edit_text(f"⚠️ Voice transcription failed: {e}")
        except Exception:
            await update.message.reply_text(f"⚠️ Voice transcription failed: {e}")

def main():
    global _LOCK_FILE
    lock_file_path = "/tmp/muse_telegram_bot.lock"
    try:
        _LOCK_FILE = open(lock_file_path, "w")
        fcntl.flock(_LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, BlockingIOError):
        print("[Telegram Bot] ⚠️ Another instance of bot.py is already running. Exiting cleanly to avoid conflict.", flush=True)
        return

    token = Config.TELEGRAM_BOT_TOKEN
    if not token:
        print("[Telegram Bot] TELEGRAM_BOT_TOKEN not configured in .env. Exiting.", flush=True)
        return

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def post_init(application) -> None:
        asyncio.create_task(watchdog.run(application.bot, Config.TELEGRAM_ALLOWED_USERS))

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, telegram.error.NetworkError):
            print(f"[Telegram Bot] Network glitch: {context.error}", flush=True)
            return
        print(f"[Telegram Bot] Warning: {context.error}", flush=True)

    request = HTTPXRequest(connect_timeout=45.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0)
    app = ApplicationBuilder().token(token).request(request).post_init(post_init).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear_chat))
    app.add_handler(CommandHandler("login", login_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("memory", profile_command))
    app.add_handler(CommandHandler("learn", learn_command))
    app.add_handler(CommandHandler("quota", quota_command))
    app.add_handler(CommandHandler("status", quota_command))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(IMAGE_FILTER, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    while True:
        try:
            print("[Telegram Bot] Bot is polling for messages...", flush=True)
            app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
            break
        except (telegram.error.TimedOut, telegram.error.NetworkError) as e:
            print(f"[Telegram Bot] Transient network error on polling: {e}. Reconnecting in 3s...", flush=True)
            time.sleep(3)
        except Exception as e:
            print(f"[Telegram Bot] Polling interrupted: {e}", flush=True)
            break

if __name__ == "__main__":
    main()