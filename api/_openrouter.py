"""Adapter exposing an OpenAI-Responses-shaped client backed by OpenRouter.

The frozen submission agents used by ClueCast call
``client.responses.parse(model=..., instructions=...,
input=..., text_format=<pydantic model>, ...)`` and read ``response.output_parsed``.
OpenRouter only speaks the Chat Completions API, so this shim translates one into
the other using JSON-schema structured output. This lets the unmodified submission
agents run against any OpenRouter model without editing ``submission/``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, Dict

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _strictify(schema: Any) -> Any:
    """Make a JSON schema acceptable to strict structured-output validators."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            props = schema.get("properties") or {}
            schema.setdefault("additionalProperties", False)
            schema["required"] = list(props.keys())
        for value in schema.values():
            _strictify(value)
    elif isinstance(schema, list):
        for value in schema:
            _strictify(value)
    return schema


def _content_text(value: Any) -> str:
    """Normalize common OpenRouter content shapes without logging their values."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(value or "")


def _parse_error_code(exc: Exception) -> str:
    """Return field/type diagnostics only; never include model output values."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            codes = []
            for item in errors(include_url=False)[:4]:
                location = ".".join(str(part) for part in item.get("loc") or ()) or "root"
                codes.append("{}:{}".format(location, item.get("type") or "invalid"))
            if codes:
                return ",".join(codes)
        except Exception:
            pass
    return type(exc).__name__


class _Responses:
    def __init__(self, api_key: str, base_url: str, headers: Dict[str, str]):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._headers = headers

    def _post(self, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("CLUECAST_OPENROUTER_API_KEY is not set")
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            "{}/chat/completions".format(self._base_url),
            data=data,
            method="POST",
            headers={
                "Authorization": "Bearer {}".format(self._api_key),
                "Content-Type": "application/json",
                **self._headers,
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format,
        timeout: float = 30.0,
        max_output_tokens: int | None = None,
        reasoning: Any = None,
        **_ignored,
    ):
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": input},
        ]
        base_body: Dict[str, Any] = {"model": model, "messages": messages}
        if max_output_tokens:
            base_body["max_tokens"] = int(max_output_tokens)
        if reasoning is not None:
            base_body["reasoning"] = reasoning

        schema = _strictify(text_format.model_json_schema())
        schema_body = dict(base_body)
        schema_body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": getattr(text_format, "__name__", "response"),
                "strict": True,
                "schema": schema,
            },
        }

        try:
            payload = self._post(schema_body, timeout)
        except urllib.error.HTTPError:
            # Some models reject strict json_schema; retry asking for a JSON object.
            fallback_body = dict(base_body)
            fallback_body["response_format"] = {"type": "json_object"}
            payload = self._post(fallback_body, timeout)

        content = _content_text(
            payload.get("choices", [{}])[0].get("message", {}).get("content")
        )
        if not content:
            return SimpleNamespace(output_parsed=None, parse_error="empty_content")
        try:
            parsed = text_format.model_validate_json(content)
        except Exception as first_error:
            # Salvage the first JSON object if the model added prose around it.
            start, end = content.find("{"), content.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return SimpleNamespace(
                    output_parsed=None,
                    parse_error=_parse_error_code(first_error),
                )
            try:
                parsed = text_format.model_validate_json(content[start : end + 1])
            except Exception as salvage_error:
                return SimpleNamespace(
                    output_parsed=None,
                    parse_error=_parse_error_code(salvage_error),
                )
        return SimpleNamespace(output_parsed=parsed, parse_error=None)


class OpenRouterClient:
    """Minimal OpenAI-Responses-compatible client backed by OpenRouter."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        referer: str | None = None,
        title: str | None = None,
        **_ignored,
    ):
        headers: Dict[str, str] = {}
        referer = referer or os.environ.get("CLUECAST_OPENROUTER_REFERER")
        title = title or os.environ.get("CLUECAST_OPENROUTER_TITLE") or "ClueCast"
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
        # Deliberately a project-scoped variable name (not the generic
        # OPENROUTER_API_KEY) so an ambient key in a local shell is never used.
        if api_key is None:
            api_key = os.environ.get("CLUECAST_OPENROUTER_API_KEY", "")
        self.responses = _Responses(
            api_key,
            base_url or os.environ.get("CLUECAST_OPENROUTER_BASE_URL") or DEFAULT_BASE_URL,
            headers,
        )
