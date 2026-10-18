import asyncio
import httpx
from core.config import Config

# Verified active models across providers
ROLE_ROUTES = {
    "router": [
        ("gemini", "gemini-3.5-flash-lite"),
        ("groq", "openai/gpt-oss-20b"),
        ("gemini", "gemini-3.1-flash-lite"),
        ("mistral", "codestral-latest"),
    ],
    "coder": [
        ("mistral", "codestral-latest"),
        ("gemini", "gemini-3.8-flash"),
        ("gemini", "gemini-3.5-flash-lite"),
        ("groq", "openai/gpt-oss-120b"),
    ],
    "chat": [
        ("gemini", "gemini-3.5-flash-lite"),
        ("groq", "openai/gpt-oss-120b"),
        ("gemini", "gemini-3.8-flash"),
        ("mistral", "codestral-latest"),
    ],
    "vision": [
        ("gemini", "gemini-3.5-flash-lite"),
        ("gemini", "gemini-3.8-flash"),
        ("mistral", "pixtral-12b-2409"),
    ],
    "heavy": [
        ("gemini", "gemini-3.8-flash"),
        ("mistral", "codestral-latest"),
        ("groq", "openai/gpt-oss-120b"),
    ],
}

async def _call_openai_compatible(
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    system: str,
    image_data: str | None = None,
    mime_type: str = "image/jpeg",
    history: list[dict] | None = None,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    messages = [{"role": "system", "content": system}]
    if history:
        for m in history:
            role = "assistant" if m.get("role") in ("assistant", "model") else "user"
            content = (m.get("content") or "").strip()
            if not content:
                continue
            messages.append({"role": role, "content": content})

    if image_data:
        user_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
        ]
    else:
        user_content = prompt

    messages.append({"role": "user", "content": user_content})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=35.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

async def _call_gemini(
    model: str,
    prompt: str,
    system: str,
    image_data: str | None = None,
    mime_type: str = "image/jpeg",
    history: list[dict] | None = None,
) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={Config.GEMINI_API_KEY}"
    contents = []
    if history:
        merged = []
        for m in history:
            role = "model" if m.get("role") in ("assistant", "model") else "user"
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if merged and merged[-1]["role"] == role:
                merged[-1]["parts"].append({"text": content})
            else:
                merged.append({"role": role, "parts": [{"text": content}]})
        # Gemini requires the first turn to be 'user'
        while merged and merged[0]["role"] == "model":
            merged.pop(0)
        contents.extend(merged)

    current_parts = [{"text": prompt}]
    if image_data:
        current_parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": image_data,
            }
        })

    # Ensure alternating roles for Gemini
    if contents and contents[-1]["role"] == "user":
        contents[-1]["parts"].extend(current_parts)
    else:
        contents.append({"role": "user", "parts": current_parts})

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.2},
    }
    async with httpx.AsyncClient(timeout=35.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates or "content" not in candidates[0]:
            raise ValueError(f"Gemini returned invalid response: {data}")
        return candidates[0]["content"]["parts"][0]["text"]

MUSE_SYSTEM_PROMPT = """You are Muse, an autonomous self-extending and self-healing AI agent running locally on the user's Linux machine with direct system access via generated Python tools in tools/.
You assist users with coding, system management, data analysis, tool synthesis, vision analysis, and general tasks.
Key attributes:
- Name: Muse (an autonomous, self-extending local agent — never refer to yourself as ChatGPT, OpenAI, or a generic cloud assistant).
- Real System Access: You have direct local system access through your Python sandbox and tool registry. NEVER claim to be a cloud AI without filesystem or local environment access.
- Self-Healing & Evolution: You write and execute local tools, auto-install missing packages on demand, learn from errors, and evolve from execution feedback.
- Error Handling: If a tool fails with '[Errno 2] No such file or directory', explain the path that was searched and suggest the user verify the exact folder name or pass an absolute path.
- Multimodal Vision: You can analyze photos, screenshots, diagrams, and OCR images.
- Memory: You maintain conversational context across multiple turns.
Be concise, proactive, intelligent, and helpful. Speak with the identity and capabilities of Muse."""

async def generate(
    role: str,
    prompt: str,
    system: str | None = None,
    image_data: str | None = None,
    mime_type: str = "image/jpeg",
    history: list[dict] | None = None,
) -> dict:
    """Routes request through providers with failover and rate-limit backoff."""
    if not system or system == "You are a helpful AI assistant.":
        if role == "vision":
            system = "You are Muse Vision, an expert AI visual analyst. Inspect the image closely and provide clear, accurate, and structured observations."
        else:
            system = MUSE_SYSTEM_PROMPT
    fallback_role = "vision" if image_data else "chat"
    routes = ROLE_ROUTES.get(role, ROLE_ROUTES[fallback_role])
    errors = []

    for provider, model in routes:
        for attempt in range(2):  # Quick 1-second retry on transient 429s
            try:
                # 1. Groq (skip vision calls unless model supports it)
                if provider == "groq" and Config.GROQ_API_KEY and not Config.GROQ_API_KEY.startswith("gsk_your"):
                    if image_data:
                        break  # Groq text models don't take image payloads
                    text = await _call_openai_compatible(
                        "https://api.groq.com/openai/v1/chat/completions",
                        Config.GROQ_API_KEY,
                        model,
                        prompt,
                        system,
                        history=history,
                    )
                    return {"text": text, "provider": provider, "model": model}

                # 2. Gemini (multimodal native)
                elif provider == "gemini" and Config.GEMINI_API_KEY and not Config.GEMINI_API_KEY.startswith("AIza_your"):
                    text = await _call_gemini(model, prompt, system, image_data=image_data, mime_type=mime_type, history=history)
                    return {"text": text, "provider": provider, "model": model}

                # 3. Mistral
                elif provider == "mistral" and Config.MISTRAL_API_KEY and not Config.MISTRAL_API_KEY.startswith("mstrl_your"):
                    if image_data and "pixtral" not in model.lower():
                        break
                    text = await _call_openai_compatible(
                        "https://api.mistral.ai/v1/chat/completions",
                        Config.MISTRAL_API_KEY,
                        model,
                        prompt,
                        system,
                        image_data=image_data,
                        mime_type=mime_type,
                        history=history,
                    )
                    return {"text": text, "provider": provider, "model": model}

                # 4. OpenRouter Free Fallback (multimodal supported)
                elif provider == "openrouter" and Config.OPENROUTER_API_KEY and not Config.OPENROUTER_API_KEY.startswith("sk-or-your"):
                    text = await _call_openai_compatible(
                        "https://openrouter.ai/api/v1/chat/completions",
                        Config.OPENROUTER_API_KEY,
                        model,
                        prompt,
                        system,
                        image_data=image_data,
                        mime_type=mime_type,
                        history=history,
                    )
                    return {"text": text, "provider": provider, "model": model}

                break  # Skip to next provider if key is not configured

            except httpx.HTTPStatusError as e:
                # If 429, wait 2 seconds and retry once before failing over
                if e.response.status_code == 429 and attempt == 0:
                    await asyncio.sleep(2.0)
                    continue
                errors.append(f"{provider}/{model}: Status {e.response.status_code}")
                break
            except Exception as e:
                errors.append(f"{provider}/{model}: {str(e)}")
                break

    raise RuntimeError(f"All providers failed for role '{role}'. Errors: {errors}")