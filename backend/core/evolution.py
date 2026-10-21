import json
import re
import asyncio
from core.router import generate
from core.user_profile import add_learned_rule, add_memory, update_profile_field
from core.journal import record_lesson

EVOLUTION_SYSTEM_PROMPT = """You are the Hermes Self-Evolution & Reflection Engine for Muse Agent on Linux.
Your job is to analyze user interactions, detect user corrections, preferences, habits, and environment details, and evolve the agent's persistent memory.

Analyze the user's message and the assistant's behavior:
1. Did the user state a personal fact, location, or habit? (e.g. "I live in Nilambur", "My primary browser is Zen")
2. Did the user correct the agent or express a negative constraint? (e.g. "Don't use browser agent, use my screen", "You killed the browser, don't do that")
3. Is there a permanent behavioral rule that Muse should follow to never repeat this mistake?

Return ONLY a valid JSON object matching:
{
  "has_learnings": true,
  "location": "City, Region or null",
  "new_rules": ["Imperative rule like 'Never use pkill to minimize apps'"],
  "new_memories": ["Factual statement like 'User uses Zen browser as primary browser'"],
  "journal_lesson": {
    "title": "Short title",
    "problem": "What went wrong",
    "solution": "Exact correct method to use"
  }
}
If no new facts, rules, or corrections are present, return: {"has_learnings": false}"""

CORRECTION_TRIGGERS = [
    "no ", "not ", "dont", "don't", "never", "instead", "prefer", "u killed", "you killed",
    "why did u", "why did you", "wrong", "stop ", "remember", "from now on", "always ",
    "i live in", "my location", "where i live", "my name", "my browser", "use my", "on my screen",
    "no no", "not at all", "didnt click", "didn't click", "still not"
]

def _has_evolution_signals(user_text: str) -> bool:
    """Fast pre-filter to avoid unnecessary LLM calls on simple chit-chat."""
    if not user_text:
        return False
    lower = user_text.lower().strip()
    return any(trigger in lower for trigger in CORRECTION_TRIGGERS)

def _parse_evolution_json(text: str) -> dict:
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
    return {"has_learnings": False}

async def reflect_and_evolve(user_message: str, assistant_reply: str, tool_trace: list[dict] | None = None):
    """
    Background reflection loop that extracts user preferences, corrections, and lessons,
    updating user_profile.json and journal.json automatically. Runs non-blockingly.
    """
    if not _has_evolution_signals(user_message):
        return

    try:
        trace_str = ""
        if tool_trace:
            trace_str = "\nTools Executed: " + ", ".join([f"{t.get('tool')}({t.get('args')})" for t in tool_trace[:3]])

        prompt = (
            f"User Message: {user_message}\n"
            f"Assistant Response: {assistant_reply[:500]}\n"
            f"{trace_str}\n\n"
            f"Extract any permanent user preferences, identity facts, or behavioral rules:"
        )

        # Use router role which waterfalls to fast low-latency models
        res = await generate("router", prompt, system=EVOLUTION_SYSTEM_PROMPT)
        data = _parse_evolution_json(res["text"])

        if not data.get("has_learnings"):
            return

        # 1. Update Location if mentioned
        loc = data.get("location")
        if loc and isinstance(loc, str) and len(loc.strip()) > 2 and loc.lower() != "null":
            update_profile_field("location", loc.strip())

        # 2. Add New Behavioral Rules
        rules = data.get("new_rules") or []
        for r in rules:
            if isinstance(r, str) and len(r.strip()) > 5:
                add_learned_rule(r.strip())

        # 3. Add Memory Facts
        memories = data.get("new_memories") or []
        for m in memories:
            if isinstance(m, str) and len(m.strip()) > 5:
                add_memory(m.strip())

        # 4. Record Journal Lesson if structured fix found
        jlesson = data.get("journal_lesson")
        if jlesson and isinstance(jlesson, dict) and jlesson.get("title") and jlesson.get("solution"):
            record_lesson(
                title=jlesson.get("title"),
                problem=jlesson.get("problem", "User correction"),
                solution=jlesson.get("solution"),
                tags=["user_evolved", "learned_feedback"]
            )

        print(f"[Hermes Evolution] 🧬 Successfully evolved profile from turn: {user_message[:60]}", flush=True)

    except Exception as e:
        print(f"[Hermes Evolution Warning] Reflection error: {e}", flush=True)
