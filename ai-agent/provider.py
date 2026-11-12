"""Gemini API Provider for the FORGE IT AI Agent Engine.

Uses the current `google-genai` SDK (`client.interactions.create`) with
`gemini-3.8-flash` (or `gemini-4-argon` for complex reasoning) and structured JSON
output via `response_format={"type": "text", "mime_type": "application/json", "schema": ...}`.

Security Rules:
- Never log, return, or expose `GEMINI_API_KEY` in error messages or responses.
- Gracefully fall back to deterministic rule-based understanding/analysis when
  no API key is configured or in offline/unit-test environments.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Protocol, Type, TypeVar

from pydantic import BaseModel

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

TModel = TypeVar("TModel", bound=BaseModel)

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

# Legacy model names that must be automatically upgraded per Gemini API guidelines
_DEPRECATED_MODEL_PREFIXES = ("gemini-2.5", "gemini-2.0", "gemini-1.5")

# Environment variable names checked for API keys (never logged or exposed)
_SECRET_ENV_VARS = (
    "GEMINI_API_KEY",
    "GEMINI_4_API_KEY",
    "GEMINI_3_8_API_KEY",
    "HYDROGEN_API_KEY",
    "GOOGLE_API_KEY",
)

# Model alias mapping for Gemini 4, Gemini 3.8, and Hydrogen / Argon / Carbon
_MODEL_ALIASES: Dict[str, str] = {
    "gemini-4": "gemini-4-argon",
    "gemini-4-hydrogen": "gemini-4-argon",
    "hydrogen": "gemini-4-argon",
    "gemini-hydrogen": "gemini-4-argon",
    "gemini-3.8": "gemini-3.8-flash",
    "3.8": "gemini-3.8-flash",
}


def _load_local_env_files() -> None:
    """Load `.env` files from repo root or `backend/.env` without requiring external packages."""
    repo_root = Path(__file__).resolve().parents[1]
    for env_path in (repo_root / ".env", repo_root / "backend" / ".env"):
        if not env_path.is_file():
            continue
        if load_dotenv is not None:
            load_dotenv(dotenv_path=env_path, override=False)
            continue
        # Fallback lightweight .env parser when python-dotenv is not installed
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError:
            pass


def normalize_gemini_model_name(model_name: Optional[str] = None) -> str:
    """Ensure only current Gemini models are used (substituting `gemini-3.8-flash` for deprecated names)."""
    if not model_name or not model_name.strip():
        return DEFAULT_GEMINI_MODEL
    cleaned = model_name.strip().lower()
    if cleaned in _MODEL_ALIASES:
        return _MODEL_ALIASES[cleaned]
    if any(cleaned.startswith(prefix) for prefix in _DEPRECATED_MODEL_PREFIXES):
        return DEFAULT_GEMINI_MODEL
    return model_name.strip()


def _scrub_secrets(message: str, api_key: Optional[str] = None) -> str:
    """Remove any accidental API key or token occurrences from error strings or logs."""
    if not message:
        return ""
    scrubbed = message
    if api_key and len(api_key) >= 6:
        scrubbed = scrubbed.replace(api_key, "[REDACTED_API_KEY]")
    for env_name in _SECRET_ENV_VARS:
        env_val = os.environ.get(env_name, "").strip()
        if env_val and len(env_val) >= 6:
            scrubbed = scrubbed.replace(env_val, "[REDACTED_API_KEY]")
    scrubbed = re.sub(r"AIza[0-9A-Za-z\-_]{20,}", "[REDACTED_API_KEY]", scrubbed)
    scrubbed = re.sub(r"AQ\.[0-9A-Za-z\-_]{20,}", "[REDACTED_API_KEY]", scrubbed)
    scrubbed = re.sub(r"ghp_[0-9A-Za-z]{20,}", "[REDACTED_TOKEN]", scrubbed)
    return scrubbed


class LLMProvider(Protocol):
    """Protocol for structured LLM generation used by the AI Agent modules."""

    @property
    def is_live_api_configured(self) -> bool:
        """Return True if a live Gemini API client is configured and ready."""
        ...

    def generate_structured(
        self,
        *,
        system_instruction: str,
        user_prompt: str,
        response_model: Type[TModel],
        temperature: float = 0.1,
    ) -> Optional[TModel]:
        """Generate a Pydantic-validated structured output or return None on fallback."""
        ...


class GeminiProvider:
    """Production Gemini API provider using `google-genai` Interactions API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        _load_local_env_files()
        self.model: str = normalize_gemini_model_name(
            model or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        )

        raw_key = api_key if api_key is not None else self._resolve_env_api_key(self.model)
        # Ignore placeholder values from .env.example
        if raw_key.strip() in ("", "your_gemini_api_key_here", "CHANGE_ME"):
            self._api_key: Optional[str] = None
        else:
            self._api_key = raw_key.strip()

        self.timeout_seconds = timeout_seconds
        self._client: Any = None

    @staticmethod
    def _resolve_env_api_key(resolved_model: str) -> str:
        """Resolve the appropriate API key from environment variables without exposing it."""
        if resolved_model.startswith("gemini-4"):
            for var in ("GEMINI_4_API_KEY", "HYDROGEN_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
                val = os.environ.get(var, "").strip()
                if val and val not in ("your_gemini_api_key_here", "CHANGE_ME"):
                    return val
        for var in _SECRET_ENV_VARS:
            val = os.environ.get(var, "").strip()
            if val and val not in ("your_gemini_api_key_here", "CHANGE_ME"):
                return val
        return ""

    @property
    def is_live_api_configured(self) -> bool:
        """Check whether a non-empty API key is available."""
        return bool(self._api_key)

    def _get_client(self) -> Any:
        """Lazily initialize `google.genai.Client` without exposing the key."""
        if self._client is not None:
            return self._client
        if not self._api_key:
            return None
        from google import genai

        self._client = genai.Client(api_key=self._api_key)
        return self._client

    def __repr__(self) -> str:
        """Safe repr that never prints the API key."""
        return (
            f"GeminiProvider(model={self.model!r}, "
            f"configured={self.is_live_api_configured})"
        )

    def generate_structured(
        self,
        *,
        system_instruction: str,
        user_prompt: str,
        response_model: Type[TModel],
        temperature: float = 0.1,
    ) -> Optional[TModel]:
        """Call Gemini Interactions API with a Pydantic JSON schema and return the parsed model.

        Returns `None` if no API key is configured or if the network call fails,
        allowing callers (`RequestUnderstandingEngine` / `ResultAnalyzer`) to
        seamlessly use deterministic fallback logic.
        """
        client = self._get_client()
        if client is None:
            return None

        try:
            interaction = client.interactions.create(
                model=self.model,
                system_instruction=system_instruction,
                input=user_prompt,
                generation_config={"temperature": temperature},
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": response_model.model_json_schema(),
                },
                timeout=self.timeout_seconds,
            )
            output_text = getattr(interaction, "output_text", None)
            if not output_text:
                return None
            return response_model.model_validate_json(output_text)
        except Exception as exc:
            # Never leak API key in logs or exceptions
            _ = _scrub_secrets(str(exc), self._api_key)
            return None


class MockGeminiProvider:
    """Configurable mock LLM provider for deterministic unit and integration testing."""

    def __init__(self, canned_responses: Optional[Dict[str, BaseModel]] = None) -> None:
        self._canned: Dict[str, BaseModel] = dict(canned_responses or {})
        self.calls: List[Dict[str, Any]] = []

    @property
    def is_live_api_configured(self) -> bool:
        return bool(self._canned)

    def register_response(self, model_cls: Type[BaseModel], instance: BaseModel) -> None:
        self._canned[model_cls.__name__] = instance

    def generate_structured(
        self,
        *,
        system_instruction: str,
        user_prompt: str,
        response_model: Type[TModel],
        temperature: float = 0.1,
    ) -> Optional[TModel]:
        self.calls.append(
            {
                "response_model": response_model.__name__,
                "user_prompt": user_prompt,
                "temperature": temperature,
            }
        )
        candidate = self._canned.get(response_model.__name__)
        if candidate is not None and isinstance(candidate, response_model):
            return candidate
        return None
