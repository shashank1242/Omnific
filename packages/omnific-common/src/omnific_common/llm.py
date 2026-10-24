"""Thin wrapper over the OpenAI Responses API. Agents must call models only through this module.

Model IDs come from env vars per role so they can change without code edits:
  MODEL_REASONING  Modeler, Solver-coder
  MODEL_GENERAL    Researcher, LLM critic (must differ from the Solver-coder's model)
  MODEL_FAST       cheap routing/classification
"""

import base64
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

from openai import AsyncOpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class Role(str, Enum):
    REASONING = "MODEL_REASONING"
    GENERAL = "MODEL_GENERAL"
    FAST = "MODEL_FAST"


class LLMError(RuntimeError):
    pass


def model_for(role: Role) -> str:
    model = os.environ.get(role.value)
    if not model:
        raise LLMError(f"{role.value} is not set")
    return model


class LLM:
    def __init__(self, client: AsyncOpenAI | None = None):
        self._client = client

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI()  # reads OPENAI_API_KEY
        return self._client

    async def structured(
        self,
        role: Role,
        instructions: str,
        input: str,
        schema: type[T],
        tools: list[dict[str, Any]] | None = None,
    ) -> T:
        """Call the model and parse its output into `schema` via structured outputs."""
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        model = model_for(role)
        resp = await self.client.responses.parse(
            model=model, instructions=instructions, input=input, text_format=schema, **kwargs
        )
        usage = getattr(resp, "usage", None)
        log.info("llm call model=%s schema=%s usage=%s", model, schema.__name__, usage)
        parsed = resp.output_parsed
        if parsed is None:
            raise LLMError(f"model returned no parseable {schema.__name__}")
        return parsed

    async def run_tools(
        self,
        role: Role,
        instructions: str,
        input: str,
        schema: type[T],
        tools: list["Tool"],
        hosted_tools: list[dict[str, Any]] | None = None,
        max_steps: int = 8,
    ) -> T:
        """Agentic loop: the model may call our function `tools` (and hosted tools such as web search)
        repeatedly, then must answer in `schema`. Each step chains on previous_response_id."""
        model = model_for(role)
        by_name = {t.name: t for t in tools}
        specs = [t.spec() for t in tools] + list(hosted_tools or [])
        resp = await self.client.responses.parse(
            model=model, instructions=instructions, input=input, text_format=schema, tools=specs
        )
        for step in range(max_steps):
            calls = [item for item in resp.output if getattr(item, "type", None) == "function_call"]
            if not calls:
                break
            outputs = []
            for call in calls:
                tool = by_name.get(call.name)
                if tool is None:
                    result = {"error": f"unknown tool {call.name}"}
                else:
                    try:
                        args = tool.args_model.model_validate_json(call.arguments or "{}")
                        result = await tool.handler(args)
                    except Exception as e:  # noqa: BLE001 - tool errors go back to the model, not up
                        log.warning("tool %s failed: %s", call.name, e)
                        result = {"error": f"{type(e).__name__}: {e}"}
                outputs.append({"type": "function_call_output", "call_id": call.call_id,
                                "output": _to_json(result)})
            log.info("llm tools step=%d calls=%s", step, [c.name for c in calls])
            resp = await self.client.responses.parse(
                model=model, instructions=instructions, input=outputs, text_format=schema,
                tools=specs, previous_response_id=resp.id,
            )
        else:
            raise LLMError(f"tool loop exceeded {max_steps} steps")
        parsed = resp.output_parsed
        if parsed is None:
            raise LLMError(f"model returned no parseable {schema.__name__}")
        return parsed

    async def describe_image(self, role: Role, prompt: str, image: bytes, mime: str) -> str:
        """Vision: describe a figure/graph/photo. Returns plain text."""
        b64 = base64.b64encode(image).decode()
        resp = await self.client.responses.create(
            model=model_for(role),
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"},
            ]}],
        )
        return resp.output_text.strip()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeddings (MODEL_EMBEDDING, EMBEDDING_DIMENSIONS), batched."""
        model = os.environ.get("MODEL_EMBEDDING", "text-embedding-3-large")
        dims = int(os.environ.get("EMBEDDING_DIMENSIONS", "1536"))
        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            resp = await self.client.embeddings.create(
                model=model, input=texts[i:i + EMBED_BATCH], dimensions=dims
            )
            out.extend(d.embedding for d in resp.data)
        return out


EMBED_BATCH = 128


@dataclass
class Tool:
    """A function tool the model may call. `handler` gets the validated args and returns JSON-able data."""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[Any], Awaitable[Any]]

    def spec(self) -> dict[str, Any]:
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": to_strict_json_schema(self.args_model), "strict": True}


def _to_json(result: Any) -> str:
    if isinstance(result, BaseModel):
        return result.model_dump_json()
    return json.dumps(result, default=str)


def web_search_tool() -> dict[str, Any]:
    """OpenAI hosted web search tool spec; type name overridable as the API evolves."""
    return {"type": os.environ.get("OPENAI_WEB_SEARCH_TOOL", "web_search")}
