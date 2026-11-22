"""ARVO — OpenRouter LLM provider. LLM nunca calcula verdade financeira."""

import time, httpx
from app.core.config import settings

class OpenRouterProvider:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.openrouter_api_key
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")

    async def generate(self, prompt: str, system: str = "", model: str | None = None, temperature: float = 0.2) -> dict:
        if not self.api_key:
            raise RuntimeError("LLM not configured: ARVO_OPENROUTER_API_KEY is empty")
        model = model or "openai/gpt-4o-mini"
        msgs = []
        if system: msgs.append({"role":"system","content": system})
        msgs.append({"role":"user","content": prompt})
        t0=time.time()
        async with httpx.AsyncClient(timeout=60) as c:
            r=await c.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type":"application/json"}, json={"model": model, "messages": msgs, "temperature": temperature})
            r.raise_for_status()
            j=r.json()
        latency=int((time.time()-t0)*1000)
        return {"content": j["choices"][0]["message"]["content"], "model": model, "usage": j.get("usage",{}), "latency_ms": latency}

def get_provider() -> OpenRouterProvider:
    return OpenRouterProvider()
