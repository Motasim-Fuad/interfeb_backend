"""Optional LLM planner. One OpenAI-compatible client covers OpenAI, Groq, Gemini, Ollama and
LM Studio. The LLM only chooses a tool and its arguments; every number still comes from the
backend tools, so switching models never changes QC calculations."""
import json
import logging
from typing import Optional

import httpx

from app.core.clock import factory_today
from app.core.config import settings

log = logging.getLogger(__name__)


def llm_enabled() -> bool:
    return bool(settings.ASSISTANT_LLM_BASE_URL and settings.ASSISTANT_LLM_MODEL)


def llm_label() -> str:
    return settings.ASSISTANT_LLM_MODEL if llm_enabled() else "Offline engine"


def plan_with_llm(message: str, history: list[dict], tools: list[dict], context_hint: str) -> Optional[dict]:
    """Returns {"tool": name, "args": {...}} or {"reply": text}; None when the LLM is off or fails."""
    if not llm_enabled():
        return None
    system = (
        f"You are {settings.ASSISTANT_NAME}, the assistant inside the Interfab fabric QC admin dashboard. "
        "Always answer data questions by calling exactly one tool; never invent numbers. "
        "Users write English, Bangla or Banglish. Dates are YYYY-MM-DD. "
        f"Today is {factory_today().isoformat()} (factory time); 'this month', 'ei mase', 'today', 'last week' "
        "and similar are relative to today. "
        f"Conversation context: {context_hint}"
    )
    messages = [{"role": "system", "content": system}]
    for turn in history[-6:]:
        messages.append({"role": turn["role"], "content": turn["content"][:1500]})
    messages.append({"role": "user", "content": message})
    headers = {"Content-Type": "application/json"}
    if settings.ASSISTANT_LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.ASSISTANT_LLM_API_KEY}"
    try:
        res = httpx.post(
            f"{settings.ASSISTANT_LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers=headers,
            timeout=settings.ASSISTANT_LLM_TIMEOUT_SECONDS,
            json={
                "model": settings.ASSISTANT_LLM_MODEL,
                "messages": messages,
                "tools": [{"type": "function", "function": t} for t in tools],
                "tool_choice": "auto",
                "temperature": 0,
            },
        )
        res.raise_for_status()
        choice = res.json()["choices"][0]["message"]
        calls = choice.get("tool_calls") or []
        if calls:
            fn = calls[0]["function"]
            args = fn.get("arguments") or "{}"
            return {"tool": fn["name"], "args": json.loads(args) if isinstance(args, str) else args}
        if choice.get("content"):
            return {"reply": choice["content"]}
    except Exception as exc:  # noqa: BLE001 - any provider failure falls back to the offline engine
        log.warning("Assistant LLM planning failed, using offline engine: %s", exc)
    return None
