"""ARVO — AIProvider. LLM nunca calcula verdade financeira. generate/extract/embed + adapters."""

import time, httpx
from typing import Any
from app.core.config import settings

class AIProvider:
    async def generate(self, prompt: str, system: str = "", model: str | None = None, temperature: float = 0.2) -> dict:
        raise NotImplementedError
    async def extract(self, text: str, schema: dict, model: str | None = None) -> dict:
        raise NotImplementedError
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

class OpenRouterProvider(AIProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.openrouter_api_key
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")

    async def generate(self, prompt: str, system: str = "", model: str | None = None, temperature: float = 0.2) -> dict:
        if not self.api_key:
            # Mock for MVP without key — deterministic stub, Python will validate
            return {"content": f"[MOCK OpenRouter] {prompt[:200]}", "model": model or "mock", "usage": {"prompt_tokens": 0, "completion_tokens": 0}, "mock": True}
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

    async def extract(self, text: str, schema: dict, model: str | None = None) -> dict:
        # Pydantic-contracted extraction via JSON mode
        sys = f"Extract JSON matching schema {schema}. Return only JSON."
        res = await self.generate(f"Text:\n{text}\n\nSchema:{schema}", system=sys, model=model, temperature=0)
        # If mock, return empty per schema keys
        if res.get("mock"):
            return {k: None for k in schema.get("properties", {}).keys()}
        import json as _json
        try: return _json.loads(res["content"])
        except Exception: return {"raw": res["content"]}

def get_provider(name: str | None = None) -> AIProvider:
    name = (name or settings.llm_provider).lower()
    if name == "openrouter": return OpenRouterProvider()
    # Future: OpenAIProvider, AnthropicProvider, GeminiProvider share same interface
    return OpenRouterProvider()
