import asyncio
import httpx
from core.config import Config

# Verified active models across providers
ROLE_ROUTES = {
    "router": [
        ("groq", "openai/gpt-oss-20b"),
        ("groq", "qwen/qwen3.8-27b"),
        ("gemini", "gemini-2.5-flash-lite"),
        ("openrouter", "openrouter/free"),
    ],
    "coder": [
        ("mistral", "codestral-latest"),
        ("groq", "openai/gpt-oss-120b"),
        ("groq", "qwen/qwen3.8-27b"),
        ("gemini", "gemini-2.5-flash-lite"),
        ("openrouter", "openrouter/free"),
    ],
    "chat": [
        ("groq", "openai/gpt-oss-120b"),
        ("groq", "qwen/qwen3.8-27b"),
        ("gemini", "gemini-2.5-flash-lite"),
        ("openrouter", "openrouter/free"),
    ],
    "heavy": [
        ("groq", "openai/gpt-oss-120b"),
        ("mistral", "codestral-latest"),
        ("gemini", "gemini-2.5-flash-lite"),
    ],
}

async def _call_openai_compatible(url: str, api_key: str, model: str, prompt: str, system: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

async def _call_gemini(model: str, prompt: str, system: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={Config.GEMINI_API_KEY}"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates or "content" not in candidates[0]:
            raise ValueError(f"Gemini returned invalid response: {data}")
        return candidates[0]["content"]["parts"][0]["text"]

async def generate(role: str, prompt: str, system: str = "You are a helpful AI assistant.") -> dict:
    """Routes request through providers with failover and rate-limit backoff."""
    routes = ROLE_ROUTES.get(role, ROLE_ROUTES["chat"])
    errors = []

    for provider, model in routes:
        for attempt in range(2):  # Quick 1-second retry on transient 429s
            try:
                # 1. Groq
                if provider == "groq" and Config.GROQ_API_KEY and not Config.GROQ_API_KEY.startswith("gsk_your"):
                    text = await _call_openai_compatible(
                        "https://api.groq.com/openai/v1/chat/completions",
                        Config.GROQ_API_KEY,
                        model,
                        prompt,
                        system,
                    )
                    return {"text": text, "provider": provider, "model": model}

                # 2. Gemini
                elif provider == "gemini" and Config.GEMINI_API_KEY and not Config.GEMINI_API_KEY.startswith("AIza_your"):
                    text = await _call_gemini(model, prompt, system)
                    return {"text": text, "provider": provider, "model": model}

                # 3. Mistral
                elif provider == "mistral" and Config.MISTRAL_API_KEY and not Config.MISTRAL_API_KEY.startswith("mstrl_your"):
                    text = await _call_openai_compatible(
                        "https://api.mistral.ai/v1/chat/completions",
                        Config.MISTRAL_API_KEY,
                        model,
                        prompt,
                        system,
                    )
                    return {"text": text, "provider": provider, "model": model}

                # 4. OpenRouter Free Fallback
                elif provider == "openrouter" and Config.OPENROUTER_API_KEY and not Config.OPENROUTER_API_KEY.startswith("sk-or-your"):
                    text = await _call_openai_compatible(
                        "https://openrouter.ai/api/v1/chat/completions",
                        Config.OPENROUTER_API_KEY,
                        model,
                        prompt,
                        system,
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