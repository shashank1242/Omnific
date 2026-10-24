"""Thin wrapper over the OpenAI Responses API. Agents must call models only through this module.

Model IDs come from env vars per role so they can change without code edits:
  MODEL_REASONING  Modeler, Solver-coder
  MODEL_GENERAL    Researcher, LLM critic (must differ from the Solver-coder's model)
  MODEL_FAST       cheap routing/classification
"""

import logging
import os
from enum import Enum
from typing import Any, TypeVar

from openai import AsyncOpenAI
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


def web_search_tool() -> dict[str, Any]:
    """OpenAI hosted web search tool spec; type name overridable as the API evolves."""
    return {"type": os.environ.get("OPENAI_WEB_SEARCH_TOOL", "web_search")}
