import os
import sys
import re
import html
import time
import json
import base64
import fcntl
import asyncio
import httpx
from telegram import Update, BotCommand
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
from core.quota_manager import get_all_cooldowns, get_model_operational_summary
from core.goal_manager import plan_goal_mission, format_goal_progress, run_goal_mission
from main import ROUTER_SYSTEM_PROMPT, SYNTHESIS_SYSTEM_PROMPT, TELEGRAM_SYNTHESIS_SYSTEM_PROMPT, _parse_json

# Module-level variable to hold file handle so it's not garbage-collected
_LOCK_FILE = None

# Active tasks registry: chat_id -> dict
# {
#     "task": asyncio.Task,
#     "status_msg": telegram.Message,
#     "prompt": str,
#     "step": int,
#     "max_steps": int,
#     "current_tool": str | None,
#     "cancel_event": asyncio.Event,
#     "start_time": float,
# }
ACTIVE_TASKS: dict[str, dict] = {}

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
        "👋 *Muse Desktop Agent is Online!*\n\n"
        "• Send tasks, system questions, or voice notes\n"
        "• Send screenshots or photos for instant vision analysis\n"
        "• Use `/goal <objective>` for autonomous multi-step goal execution\n"
        "• Use `/stop` to halt any ongoing task or goal immediately\n"
        "• Use `/status` to view active task progress & model quotas\n"
        "• Use `/screen` to capture and inspect the active desktop\n"
        "• Use `/clear` to reset conversation context\n"
        "• Use `/profile` to view your evolved preferences\n"
        "• Use `/help` to see all commands",
        parse_mode="Markdown",
    )

async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets a high-level autonomous goal that Muse executes iteratively until completed."""
    if not await is_authorized(update):
        return

    chat_id = str(update.effective_chat.id)

    # 1. If no objective argument provided, check active goal or show usage
    if not context.args:
        task_info = ACTIVE_TASKS.get(chat_id)
        if task_info and task_info.get("is_goal") and task_info.get("goal_obj") and not task_info["task"].done():
            card = format_goal_progress(task_info["goal_obj"])
            await update.message.reply_text(card, parse_mode="Markdown")
            return

        usage_text = (
            "🎯 *Muse Autonomous Goal Mode*\n\n"
            "Assign an objective and Muse will autonomously plan, execute, adapt, and verify each step until completed!\n\n"
            "*Usage:*\n"
            "`/goal <your objective>`\n\n"
            "*Examples:*\n"
            "• `/goal open youtube in chrome and play one sun one moon`\n"
            "• `/goal check system health, top memory processes, and write a summary`\n"
            "• `/goal find the weather in Nilambur and notify me`\n\n"
            "👉 While a goal is running, send `/stop` at any time to halt it."
        )
        await update.message.reply_text(usage_text, parse_mode="Markdown")
        return

    objective = " ".join(context.args).strip()

    # 2. Check if a task or goal is already executing in this chat
    if chat_id in ACTIVE_TASKS and not ACTIVE_TASKS[chat_id]["task"].done():
        prev = ACTIVE_TASKS[chat_id]
        if prev.get("cancel_event"):
            prev["cancel_event"].set()
        if prev.get("task") and not prev["task"].done():
            prev["task"].cancel()
        await update.message.reply_text("🔄 Halting previous mission to initiate your new goal...", quote=True)
        await asyncio.sleep(0.2)

    status_msg = await update.message.reply_text(f"🎯 *Formulating autonomous mission plan for:* \"_{objective[:60]}..._\"...", parse_mode="Markdown")

    cancel_event = asyncio.Event()
    current_task = asyncio.current_task()

    try:
        # Phase 1: Planning mission milestones
        goal = await plan_goal_mission(objective)

        ACTIVE_TASKS[chat_id] = {
            "task": current_task,
            "status_msg": status_msg,
            "prompt": f"GOAL: {objective}",
            "is_goal": True,
            "goal_obj": goal,
            "cancel_event": cancel_event,
            "start_time": time.time(),
        }

        # Send initial mission plan card
        initial_card = format_goal_progress(goal)
        await safe_edit_text(status_msg, initial_card, parse_mode="Markdown")

        async def status_updater(card_text: str):
            await safe_edit_text(status_msg, card_text, parse_mode="Markdown")

        # Phase 2: Autonomous execution loop
        result = await run_goal_mission(goal, status_updater, cancel_event)

        if cancel_event.is_set() or result.get("status") == "stopped":
            await safe_edit_text(status_msg, f"🛑 *Mission Halted by User Request:*\n\nObjective: \"_{objective}_\"", parse_mode="Markdown")
            return

        # Phase 3: Deliver mission conclusion
        summary = result.get("summary", "Mission completed.")
        evidence = result.get("verification_evidence", "Verified.")
        iter_count = result.get("current_iteration", 1)
        elapsed = int(time.time() - result.get("start_time", time.time()))

        report = (
            f"🏁 *Goal Accomplished!*\n\n"
            f"🎯 *Objective:* \"_{objective}_\"\n\n"
            f"📝 *Summary:* {summary}\n\n"
            f"🔍 *Verification Proof:* {evidence}\n\n"
            f"📊 *Mission Stats:* Completed in {iter_count} steps ({elapsed}s)"
        )

        img_to_send = result["images_collected"][-1] if result.get("images_collected") else None
        await deliver_response(update, status_msg, report, image_file=img_to_send)

    except asyncio.CancelledError:
        print(f"[Telegram Bot] Goal cancelled for chat {chat_id}", flush=True)
        try:
            await safe_edit_text(status_msg, f"🛑 *Goal Aborted:* \"_{objective[:50]}..._\"", parse_mode="Markdown")
        except Exception:
            pass
    except Exception as e:
        print(f"[Telegram Bot] Goal error: {e}", flush=True)
        try:
            await status_msg.edit_text(f"⚠️ Goal Execution Error: {str(e)}")
        except Exception:
            await update.message.reply_text(f"⚠️ Goal Execution Error: {str(e)}")
    finally:
        ACTIVE_TASKS.pop(chat_id, None)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Halts and cancels any currently executing task for the user."""
    if not await is_authorized(update):
        return

    chat_id = str(update.effective_chat.id)
    task_info = ACTIVE_TASKS.get(chat_id)
    if not task_info or (task_info.get("task") and task_info["task"].done()):
        ACTIVE_TASKS.pop(chat_id, None)
        await update.message.reply_text("ℹ️ No active task is currently running. Muse is idle and ready.")
        return

    print(f"[Telegram Bot] Stopping active task for chat {chat_id} upon user request", flush=True)

    cancel_event: asyncio.Event = task_info.get("cancel_event")
    if cancel_event:
        cancel_event.set()

    task: asyncio.Task = task_info.get("task")
    if task and not task.done():
        task.cancel()

    status_msg = task_info.get("status_msg")
    if status_msg:
        try:
            await safe_edit_text(status_msg, "🛑 *Task halted by user request.*", parse_mode="Markdown")
        except Exception:
            pass

    prompt_snippet = task_info.get("prompt", "")[:50]
    await update.message.reply_text(
        f"🛑 *Task Stopped!*\n\n"
        f"Prompt: \"_{prompt_snippet}..._\"\n\n"
        f"Muse has halted all running actions and returned to idle.",
        parse_mode="Markdown",
    )
    ACTIVE_TASKS.pop(chat_id, None)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays active task status, running tool, and operational model quotas."""
    if not await is_authorized(update):
        return

    chat_id = str(update.effective_chat.id)
    task_info = ACTIVE_TASKS.get(chat_id)

    if task_info and not task_info.get("task").done():
        if task_info.get("is_goal") and task_info.get("goal_obj"):
            card = format_goal_progress(task_info["goal_obj"])
            await update.message.reply_text(card, parse_mode="Markdown")
            return

    lines = ["⚡ *Muse Status & Activity*"]
    if task_info and not task_info.get("task").done():
        elapsed = int(time.time() - task_info.get("start_time", time.time()))
        lines.extend([
            "",
            "🏃 *Current Activity:* EXECUTING TASK",
            f"• *Prompt:* \"_{task_info.get('prompt', '')[:60]}..._\"",
            f"• *Progress:* Step {task_info.get('step', 1)} of {task_info.get('max_steps', 4)}",
            f"• *Current Tool:* `{task_info.get('current_tool') or 'Planning / Visual Inspection'}`",
            f"• *Elapsed Time:* {elapsed}s",
            "",
            "👉 *Need to abort?* Send `/stop` to halt immediately.",
        ])
    else:
        lines.extend([
            "",
            "🟢 *Current Activity:* IDLE (Ready for tasks)",
        ])

    lines.append("")
    summary = get_model_operational_summary()
    lines.append(f"🤖 *Active Engine:* `{summary['active_model']}`")

    cooling = summary.get("cooling_models", [])
    if cooling:
        lines.append(f"⛔ *Cooldown ({len(cooling)}):* " + ", ".join([f"`{m['key']}` ({m['cooldown_remaining']})" for m in cooling]))
    else:
        lines.append("✅ *Models Out of Quota:* None")

    healthy = summary.get("healthy_models", [])
    if healthy:
        top_models = [f"`{m['key']}`: {m['remaining_requests']} left" for m in healthy[:3]]
        lines.append(f"🟢 *Remaining Quota:* " + ", ".join(top_models))

    manifest = load_local_manifest()
    lines.append(f"🛠️ *Loaded Tools ({len(manifest)}):* `" + ", ".join(list(manifest.keys())[:6]) + "...`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def screen_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Captures live desktop screenshot and delivers it to Telegram with instant AI analysis."""
    if not await is_authorized(update):
        return

    status_msg = await update.message.reply_text("📸 Capturing active desktop screen (1920x1080)...")
    loop = asyncio.get_running_loop()
    success, output = await loop.run_in_executor(None, run_tool, "take_screenshot", {})
    if not success:
        await status_msg.edit_text(f"❌ Failed to capture screenshot: {output}")
        return

    img_path = extract_image_from_tool_output(output)
    if not img_path or not os.path.isfile(img_path):
        await status_msg.edit_text(f"❌ Screenshot file not found: {output}")
        return

    await safe_edit_text(status_msg, "👁️ Analyzing screen with AI Vision...", parse_mode="Markdown")
    try:
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        vis_res = await generate(
            "vision",
            "Describe what is currently visible on the active 1920x1080 desktop: active window, visible media/video playback, and system state.",
            image_data=b64,
            mime_type="image/png",
        )
        caption = f"🖥️ *Active Desktop (1920x1080)*\n\n{vis_res.get('text', '')}"
        await deliver_response(update, status_msg, caption, image_file=img_path)
    except Exception:
        await deliver_response(update, status_msg, f"🖥️ Desktop captured: `{os.path.basename(img_path)}`", image_file=img_path)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the clean, curated list of Muse commands."""
    if not await is_authorized(update):
        return
    help_text = (
        "🤖 *Muse Desktop Agent Commands:*\n\n"
        "• `/goal <objective>` — Autonomous goal mode (plans, executes, & verifies until done)\n"
        "• `/stop` — Halt and cancel the currently running task or goal immediately\n"
        "• `/status` — View current agent activity, goal checklist, and model quotas\n"
        "• `/screen` — Capture live desktop screenshot and see what's on screen\n"
        "• `/clear` — Clear conversation memory and start fresh\n"
        "• `/profile` — View autonomous Hermes profile and learned preferences\n"
        "• `/learn <rule>` — Teach Muse a new preference or rule directly\n"
        "• `/quota` — Full model rate limits and circuit breaker diagnostics\n"
        "• `/login <url>` — Open desktop browser window to authenticate web services\n"
        "• `/help` — Show this command reference\n\n"
        "💬 *Tips:*\n"
        "• You can also send voice notes or images anytime!\n"
        "• If a task or goal is executing, simply reply `stop` or `/stop` to halt it instantly."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

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
    """Displays which models are active, out of quota, remaining requests, and available tools."""
    if not await is_authorized(update):
        return

    summary = get_model_operational_summary()
    manifest = load_local_manifest()
    tool_names = list(manifest.keys())

    lines = [
        "⚡ *Muse Operational Engine & Quota Status*",
        "",
        f"🤖 *Active Engine:* `{summary['active_model']}`",
    ]

    last_roles = summary.get("last_used_by_role", {})
    if last_roles:
        role_items = [f"`{r}`: {m}" for r, m in last_roles.items() if r != "global"]
        if role_items:
            lines.append(f"• *Role Routing:* " + ", ".join(role_items))

    lines.append("")

    cooling = summary.get("cooling_models", [])
    if cooling:
        lines.append(f"⛔ *Models Out of Quota / Cooling Down ({len(cooling)}):*")
        for m in cooling:
            lines.append(f"• `{m['key']}`\n  ⏳ *{m['cooldown_remaining']}* left | 0 reqs left\n  _({m['reason']})_")
        lines.append("")
    else:
        lines.append("✅ *Out of Quota:* None (all models operational)\n")

    healthy = summary.get("healthy_models", [])
    lines.append(f"🟢 *Active Models & Remaining Requests Today:*")
    for m in healthy:
        vis = " 👁️" if m.get("vision") else ""
        lines.append(f"• `{m['key']}`{vis}: *{m['remaining_requests']}* / {m['daily_limit']} left today ({m['rpm']} RPM)")

    lines.append("")
    lines.append(f"🛠️ *Available Tools ({len(tool_names)}):*")
    lines.append("`" + ", ".join(tool_names) + "`")

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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, custom_prompt: str | None = None):
    if not update.message:
        return
    if not custom_prompt and not update.message.text:
        return

    if not await is_authorized(update):
        return

    chat_id = str(update.effective_chat.id)
    session_id = f"tg_{chat_id}"
    user_prompt = (custom_prompt or update.message.text or "").strip()

    # 1. Check if user typed a stop phrase while a task is running
    if user_prompt.lower() in ("stop", "cancel", "halt", "abort", "/stop", "/cancel"):
        if chat_id in ACTIVE_TASKS and not ACTIVE_TASKS[chat_id]["task"].done():
            await stop_command(update, context)
            return

    # 2. If a task is already executing in this chat, cleanly halt previous before starting new
    if chat_id in ACTIVE_TASKS and not ACTIVE_TASKS[chat_id]["task"].done():
        prev = ACTIVE_TASKS[chat_id]
        if prev.get("cancel_event"):
            prev["cancel_event"].set()
        if prev.get("task") and not prev["task"].done():
            prev["task"].cancel()
        await update.message.reply_text("🔄 Halting previous task to execute your new instruction...", quote=True)
        await asyncio.sleep(0.1)

    print(f"[Telegram Bot] Received request from {chat_id}: {user_prompt[:80]}", flush=True)
    status_msg = await update.message.reply_text("🤔 Routing request...")

    cancel_event = asyncio.Event()
    current_task = asyncio.current_task()
    max_steps = 4

    ACTIVE_TASKS[chat_id] = {
        "task": current_task,
        "status_msg": status_msg,
        "prompt": user_prompt,
        "step": 0,
        "max_steps": max_steps,
        "current_tool": "Routing & Planning",
        "cancel_event": cancel_event,
        "start_time": time.time(),
    }

    try:
        manifest = load_local_manifest()
        history_context = format_history_context(session_id)
        user_profile_context = get_user_profile_context()

        step_count = 0
        tool_trace = []
        combined_raw = []
        images_collected = []
        created_tool_name = None
        created_tool_info = None

        while step_count < max_steps:
            if cancel_event.is_set():
                raise asyncio.CancelledError()

            step_count += 1
            if chat_id in ACTIVE_TASKS:
                ACTIVE_TASKS[chat_id]["step"] = step_count
                ACTIVE_TASKS[chat_id]["current_tool"] = "Routing & Planning"
            tool_names = list(manifest.keys())
            steps_left = max(0, max_steps - step_count)
            tools_context = f"LOCAL TOOL REGISTRY: {len(tool_names)} tools loaded ({', '.join(tool_names)})\nEXECUTION BUDGET: Step {step_count} of {max_steps} ({steps_left} steps remaining in this turn)"

            if not tool_trace:
                planner_prompt = (
                    f"{user_profile_context}\n\n"
                    f"{tools_context}\n\n"
                    f"Conversation History:\n{history_context}\n\n"
                    f"Available Tools Schema:\n{json.dumps(manifest)}\n\n"
                    f"Current user request: {user_prompt}"
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
                    f"Original User Request: {user_prompt}\n\n"
                    f"Based on completed steps and output above, decide the next action: USE_TOOL, FINISH, or MAKE_TOOL (Remember: verify desktop/system actions before finishing!):"
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

                if chat_id in ACTIVE_TASKS:
                    ACTIVE_TASKS[chat_id]["current_tool"] = t_name

                await safe_edit_text(status_msg, f"⚙️ Step {step_count}: Running `{t_name}`...", parse_mode="Markdown")

                if cancel_event.is_set():
                    raise asyncio.CancelledError()

                print(f"[Telegram Bot] Step {step_count}: Running '{t_name}' with {tool_args}", flush=True)
                loop = asyncio.get_running_loop()
                success, output = await loop.run_in_executor(None, run_tool, t_name, tool_args)
                print(f"[Telegram Bot] Step {step_count}: Tool '{t_name}' success={success} (output {len(output)} chars)", flush=True)

                if cancel_event.is_set():
                    raise asyncio.CancelledError()

                if t_name == "take_screenshot" and success:
                    img_path = extract_image_from_tool_output(output)
                    if img_path and os.path.isfile(img_path):
                        await safe_edit_text(status_msg, f"👁️ Step {step_count}: Analyzing screen visually with AI...", parse_mode="Markdown")
                        vis_prompt = (
                            f"You are the visual cortex for Muse Agent on Ubuntu Linux desktop.\n"
                            f"The user's current request is: '{user_prompt}'\n\n"
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
                            print(f"[Telegram Bot] Step {step_count}: AI Vision analyzed screenshot ({len(vis_analysis)} chars)", flush=True)
                        except Exception as ve:
                            print(f"[Telegram Bot] Vision analysis error on screenshot: {ve}", flush=True)

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
            f"Provide the final structured answer for the user on Telegram. Remember: State exact verification evidence truthfully, report any tool errors completely, and never bluff task completion."
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

    except asyncio.CancelledError:
        print(f"[Telegram Bot] Task execution cleanly cancelled for chat {chat_id}", flush=True)
        try:
            await safe_edit_text(status_msg, "🛑 *Task halted by user request.*", parse_mode="Markdown")
        except Exception:
            pass
        return
    except Exception as e:
        print(f"[Telegram Bot] Message error: {e}", flush=True)
        try:
            await status_msg.edit_text(f"⚠️ Error: {str(e)}")
        except Exception:
            await update.message.reply_text(f"⚠️ Error: {str(e)}")
    finally:
        ACTIVE_TASKS.pop(chat_id, None)

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

        await handle_message(update, context, custom_prompt=transcribed_text)
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
        # Register Muse's clean command menu, eliminating old Hermes commands
        commands = [
            BotCommand("goal", "Run an autonomous multi-step mission until objective is complete"),
            BotCommand("stop", "Halt and cancel the currently running task immediately"),
            BotCommand("status", "Show active task state, running tool, and model quotas"),
            BotCommand("screen", "Capture desktop screenshot and view current screen"),
            BotCommand("clear", "Reset conversation memory and start fresh"),
            BotCommand("profile", "View autonomous user profile & learned preferences"),
            BotCommand("learn", "Teach Muse a new preference or rule directly"),
            BotCommand("quota", "Inspect model rate limits & circuit breakers"),
            BotCommand("login", "Open desktop browser window to authenticate web services"),
            BotCommand("help", "Show Muse commands and capabilities"),
        ]
        try:
            await application.bot.set_my_commands(commands)
            print("[Telegram Bot] ✅ Clean Muse slash commands registered with Telegram.", flush=True)
        except Exception as ce:
            print(f"[Telegram Bot] Warning: Failed to set Telegram bot commands: {ce}", flush=True)

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
    app.add_handler(CommandHandler("goal", goal_command))
    app.add_handler(CommandHandler("mission", goal_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("cancel", stop_command))
    app.add_handler(CommandHandler("abort", stop_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("screen", screen_command))
    app.add_handler(CommandHandler("screenshot", screen_command))
    app.add_handler(CommandHandler("clear", clear_chat))
    app.add_handler(CommandHandler("reset", clear_chat))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("memory", profile_command))
    app.add_handler(CommandHandler("learn", learn_command))
    app.add_handler(CommandHandler("quota", quota_command))
    app.add_handler(CommandHandler("login", login_command))
    app.add_handler(CommandHandler("help", help_command))
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