"""The deliberately narrow, structured-output LLM boundary.

This module is the only production code in the agent layer that knows about an
LLM SDK.  Tests supply a ``StructuredLLM`` fake, keeping agent tests fully
deterministic and keeping an SDK import out of ``esp_engine``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StructuredLLM(Protocol):
    """A model capable of returning one JSON object conforming to a supplied schema."""

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
    ) -> str:
        """Return the raw JSON object emitted by the model."""


class OpenAIResponsesLLM:
    """Production adapter using the sandbox's credential-injected Responses API.

    ``OpenAI()`` reads credentials injected by the runtime (for example a server
    started with ``llm-api:website``); no provider key is stored in source code.
    The engine does not import this class.
    """

    def __init__(self, model: str = "gpt_5_1") -> None:
        self._model = model

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
    ) -> str:
        # Import lazily so unit tests and pure intake validation do not need an
        # LLM SDK or credentials merely to import the package.
        from openai import OpenAI

        client = OpenAI()
        response = client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                }
            },
        )
        output = getattr(response, "output_text", None)
        if not isinstance(output, str) or not output.strip():
            raise ValueError("LLM Responses API returned no structured output text.")
        # Validate the adapter contract early. Pydantic validates against the
        # caller's schema separately, where malformed output is a normal result.
        json.loads(output)
        return output


class PplxStructuredLLM:
    """Production adapter over the platform LLM SDK (`pplx.python.sdks.llm_api`).

    Preferred over :class:`OpenAIResponsesLLM` because it authenticates from
    ``PPLX_LLM_API_ADDRESS`` / ``PPLX_LLM_API_KEY``, which are present both inside
    a running server and in a plain shell. That difference matters more than it
    looks: the agent layer's safety behaviour is only trustworthy if it can be
    exercised from the command line, and an adapter that works exclusively inside
    a web server makes the narrative guard effectively untestable against a real
    model.

    Uses provider-side structured output, so schema conformance is enforced
    before the response reaches the guard.
    """

    def __init__(self, model: str = "claude_sonnet_4_6") -> None:
        self._model = model

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
    ) -> str:
        import asyncio

        return asyncio.run(
            self._agenerate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                schema_name=schema_name,
            )
        )

    async def _agenerate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
    ) -> str:
        # Imported lazily: the engine and the offline tests must never require an
        # LLM SDK or credentials merely to import this package.
        from pplx.python.sdks.llm_api import (
            Client,
            Conversation,
            Identity,
            LLMAPIClient,
            ResponseFormat,
            SamplingParams,
            StructuredOutputParams,
            TextBlock,
        )

        convo = Conversation()
        convo.add_user([TextBlock(text=f"{system_prompt}\n\n{user_prompt}")])

        result = await LLMAPIClient().messages.create(
            model=self._model,
            convo=convo,
            identity=Identity(client=Client.ASI, use_case="esp_design_agent"),
            sampling_params=SamplingParams(max_tokens=4096, temperature=0.0),
            structured_output_params=StructuredOutputParams(
                response_format=ResponseFormat(
                    name=schema_name, schema=_strict_schema(dict(schema))
                )
            ),
        )

        # Use the accumulated ``text`` property rather than filtering
        # ``result.content`` by type: the SDK returns ``TextBlockEvent`` objects
        # there, not ``TextBlock``, so an isinstance filter silently yields an
        # empty string and the guard then reports a phantom "malformed output"
        # refusal that looks like a model failure.
        text = (result.text or "").strip()
        if not text:
            raise ValueError("LLM returned no structured output text.")
        json.loads(text)  # Fail fast on adapter-contract breach.
        return text


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON schema acceptable to strict structured-output modes.

    Providers in strict mode require ``additionalProperties: false`` and demand
    that every declared property appear in ``required``. Pydantic omits both for
    fields with defaults, so the schema is normalised here rather than by
    distorting the domain models to suit a transport detail.
    """
    if not isinstance(schema, dict):
        return schema

    if schema.get("type") == "object" and "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema["properties"].keys())

    # Strict structured-output modes reject numeric range and string-format
    # keywords. Dropping them here is safe because they are re-checked where it
    # actually matters: Pydantic validates the model against the full schema on
    # the way in, so a model that returns an out-of-range confidence is still
    # rejected. This only relaxes what the provider is asked to enforce.
    for keyword in (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minItems",
        "maxItems",
    ):
        schema.pop(keyword, None)

    for key, value in list(schema.items()):
        if isinstance(value, dict):
            schema[key] = _strict_schema(value)
        elif isinstance(value, list):
            schema[key] = [
                _strict_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
    return schema
