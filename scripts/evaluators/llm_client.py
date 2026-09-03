# Vendored from the orchestration repo; imports made package-local. See __init__.py.
# evaluate.llm_client
#
# Synchronous client for an OpenAI-compatible LLM endpoint (Ollama, vLLM,
# OpenAI, or any service that speaks the /v1/chat/completions contract).
#
# The client sends a system + user message pair, requests JSON output, and
# parses the structured response. It is provider-neutral: the only requirement
# is that the endpoint accepts the ``response_format: {type: "json_object"}``
# hint and returns a ``choices[0].message.content`` JSON string.
#
# Configuration is env-driven (see ``LLMSettings``). The default URL points at
# a local Ollama instance.

import json
import logging
import os
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMSettings:
    """Connection settings for the LLM endpoint."""

    url: str
    model: str
    api_key: str = ""
    timeout_seconds: float = 120.0

    @classmethod
    def from_env(cls) -> "LLMSettings":
        url = os.getenv("LLM_URL", "http://localhost:11434/v1/chat/completions").strip()
        model = os.getenv("LLM_MODEL", "").strip()
        if not model:
            raise ValueError("LLM_MODEL is required (e.g. 'qwen3', 'llama3.1')")
        return cls(
            url=url,
            model=model,
            api_key=os.getenv("LLM_API_KEY", "").strip(),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        )


class LLMClient:
    """Call an OpenAI-compatible chat endpoint and return parsed JSON."""

    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self.session = requests.Session()

    @classmethod
    def from_env(cls) -> "LLMClient":
        return cls(LLMSettings.from_env())

    def query(self, *, system: str, user: str) -> dict:
        """Send a system + user message pair and return the parsed JSON response.

        Raises on HTTP errors, JSON parse failures, or timeouts.
        """
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        response = self.session.post(
            self.settings.url,
            headers=headers,
            json={
                "model": self.settings.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=(5.0, self.settings.timeout_seconds),
        )
        response.raise_for_status()
        upstream = response.json()
        content = upstream["choices"][0]["message"]["content"]
        # Strip <think>...</think> blocks that some models (qwen3) emit in
        # "thinking" mode before the actual JSON payload.
        import re
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        result = json.loads(content)
        logger.debug("LLM response: %s", result)
        return result
