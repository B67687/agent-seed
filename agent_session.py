"""agent_session.py — lightweight API session wrapper.
Replaces mini-swe-agent DefaultAgent. Makes single-turn OpenAI-compatible calls
with safety enforcement, token tracking, and model routing from .model-config.json.
"""

import json
import os
import time
from pathlib import Path
from openai import OpenAI


class AgentSession:
    """Single-turn agent session. Not a multi-turn conversation."""

    def __init__(self, route_name: str = "general"):
        self.route_name = route_name
        resolved = self.resolve_route(route_name)
        self.model = resolved["model"]
        self.base_url = resolved["base_url"]
        api_key = resolved["api_key"]
        self.access = resolved.get("access", "read_write")

        self.client = OpenAI(base_url=self.base_url, api_key=api_key, timeout=60.0)
        self._usage = {}

    def run(self, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
        """Make a single API call. Returns response text."""
        t0 = time.time()
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.8,
            )
            elapsed = time.time() - t0
            txt = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            if usage:
                self._usage = {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                }
            return txt
        except Exception as e:
            raise RuntimeError(
                f"API call failed (route={self.route_name}, model={self.model}, "
                f"elapsed={time.time() - t0:.0f}s): {e}"
            ) from e

    @property
    def usage(self) -> dict:
        return dict(self._usage)

    @staticmethod
    def resolve_route(route_name: str) -> dict:
        """Load .model-config.json and resolve route -> {model, base_url, api_key, access}."""
        config_path = Path(__file__).parent / ".model-config.json"
        if not config_path.exists():
            # Fallback defaults
            return {
                "model": os.environ.get("LLM_MODEL", "deepseek-chat"),
                "base_url": os.environ.get(
                    "LLM_API_URL",
                    os.environ.get(
                        "AGENT_SEED_API_BASE", "https://api.deepseek.com/v1"
                    ),
                ),
                "api_key": os.environ.get(
                    "LLM_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "")
                ),
                "access": "read_write",
            }

        with open(config_path) as f:
            config = json.load(f)

        # Find the route
        routes = config.get("routes", {})
        route_config = routes.get(route_name, {})

        model_name = route_config.get("model", "")
        access = route_config.get("access", "read_write")

        # If no route found, try fallback chain
        if not model_name:
            fallback_chain = config.get("fallback_chain", [])
            for fb_model in fallback_chain:
                # Search providers for this model
                for provider_name, provider in config.get("providers", {}).items():
                    if fb_model in provider.get("models", {}):
                        model_name = fb_model
                        break
                if model_name:
                    break
            if not model_name:
                model_name = fallback_chain[0] if fallback_chain else "deepseek-chat"

        # Find the provider that serves this model
        provider_name = route_config.get("provider", "")
        base_url = ""
        api_key = ""

        if provider_name and provider_name in config.get("providers", {}):
            provider = config["providers"][provider_name]
            base_url = provider.get("base_url", "")
            api_key_env = provider.get("api_key_env", "")
            if api_key_env:
                api_key = os.environ.get(api_key_env, "")
        else:
            # Search all providers for the model
            for pname, provider in config.get("providers", {}).items():
                if model_name in provider.get("models", {}):
                    base_url = provider.get("base_url", "")
                    api_key_env = provider.get("api_key_env", "")
                    if api_key_env:
                        api_key = os.environ.get(api_key_env, "")
                    break

        return {
            "model": model_name,
            "base_url": base_url,
            "api_key": api_key,
            "access": access,
        }
