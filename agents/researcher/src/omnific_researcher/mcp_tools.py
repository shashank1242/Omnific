"""MCP gateway client: exposes MCP server tools as omnific function tools (strict-schema compatible)."""

import asyncio
import fnmatch
import keyword
import logging
import os
import re
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Literal, Optional

from omnific_common.llm import Tool
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, Field, create_model

log = logging.getLogger(__name__)

MAX_RESULT = 8 * 1024
MAX_NAME = 64


class UnsupportedSchema(ValueError):
    pass


def tool_name(raw: str) -> str:
    return ("mcp__" + re.sub(r"[^a-zA-Z0-9_-]", "_", raw))[:MAX_NAME]


# Never exposed, whatever the allowlist says: sources that distribute copyrighted papers without permission.
DENYLIST = ("*scihub*", "*sci_hub*", "*sci-hub*", "*libgen*")


def allowed(raw: str, allowlist: list[str]) -> bool:
    if any(fnmatch.fnmatchcase(raw.lower(), g) for g in DENYLIST):
        return False
    return any(fnmatch.fnmatchcase(raw, g) for g in allowlist)


def parse_allowlist(value: str | None) -> list[str]:
    globs = [g.strip() for g in (value or "*").split(",") if g.strip()]
    return globs or ["*"]


def _field_name(raw: str, used: set[str]) -> str:
    name = re.sub(r"\W", "_", raw)
    if not name or name[0].isdigit() or name.startswith(("_", "model_")) or keyword.iskeyword(name):
        name = "f_" + name
    while name in used:
        name += "_"
    used.add(name)
    return name


def _resolve(schema: dict, root: dict, depth: int = 0) -> dict:
    if depth > 20:
        raise UnsupportedSchema("recursive $ref")
    ref = schema.get("$ref")
    if ref is None:
        return schema
    if not ref.startswith("#/"):
        raise UnsupportedSchema(f"external $ref {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return _resolve(node, root, depth + 1)


def _type_for(schema: dict, root: dict, name: str, depth: int = 0) -> Any:
    """Return a python type for a JSON schema node (without the outer nullable wrapper)."""
    if depth > 6:
        raise UnsupportedSchema("schema nested too deeply")
    schema = _resolve(schema, root)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            variants = [v for v in schema[key] if _resolve(v, root).get("type") != "null"]
            if len(variants) != 1:
                raise UnsupportedSchema(f"{key} with several non-null variants")
            return _type_for(variants[0], root, name, depth + 1)
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _type_for(schema["allOf"][0], root, name, depth + 1)
    if "enum" in schema:
        vals = [v for v in schema["enum"] if v is not None]
        if not vals or not all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in vals):
            raise UnsupportedSchema("unsupported enum values")
        return Literal[tuple(vals)]  # type: ignore[return-value]
    t = schema.get("type")
    if isinstance(t, list):
        t = [x for x in t if x != "null"]
        if len(t) != 1:
            raise UnsupportedSchema(f"multi-type {t}")
        t = t[0]
    if t == "string":
        return str
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            raise UnsupportedSchema("array without items")
        return list[_type_for(items, root, name + "_item", depth + 1)]  # type: ignore[misc]
    if t == "object" or "properties" in schema:
        return _object_model(schema, root, name, depth + 1)
    raise UnsupportedSchema(f"unsupported schema {schema!r:.80}")


def _object_model(schema: dict, root: dict, name: str, depth: int = 0) -> type[BaseModel]:
    props = schema.get("properties")
    if not props:
        raise UnsupportedSchema("object without properties (free-form)")
    fields: dict[str, Any] = {}
    used: set[str] = set()
    for raw, sub in props.items():
        sub = _resolve(sub, root)
        typ = _type_for(sub, root, f"{name}_{raw}", depth)
        desc = sub.get("description") or None
        # strict mode: every property required; optional ones become nullable
        fields[_field_name(raw, used)] = (
            Optional[typ], Field(..., alias=raw, description=desc))  # noqa: UP045
    return create_model(re.sub(r"\W", "_", name) or "Args", **fields)


def args_model_for(tool_raw_name: str, input_schema: dict) -> type[BaseModel]:
    """Build a strict-compatible args model from an MCP inputSchema; raises UnsupportedSchema."""
    schema = input_schema or {"type": "object", "properties": {}}
    if not schema.get("properties"):
        # no arguments: a model with zero fields is a valid strict object
        model = create_model("Args_" + re.sub(r"\W", "_", tool_raw_name))
    else:
        model = _object_model(schema, schema, "Args_" + re.sub(r"\W", "_", tool_raw_name))
    try:
        to_strict_json_schema(model)
    except Exception as e:
        raise UnsupportedSchema(f"strict schema failed: {e}") from e
    return model


def _text(result: Any) -> str:
    parts = []
    for c in getattr(result, "content", None) or []:
        text = getattr(c, "text", None)
        parts.append(text if text is not None else f"[{getattr(c, 'type', 'content')}]")
    out = "\n".join(parts)
    if getattr(result, "isError", False):
        out = "ERROR: " + out
    if len(out) > MAX_RESULT:
        out = out[:MAX_RESULT] + "\n...[truncated]"
    return out


def make_tool(session: Any, raw_name: str, description: str, input_schema: dict) -> Tool:
    model = args_model_for(raw_name, input_schema)

    async def handler(args: BaseModel) -> str:
        payload = args.model_dump(by_alias=True, exclude_none=True)
        return _text(await session.call_tool(raw_name, payload))

    return Tool(name=tool_name(raw_name), description=(description or raw_name)[:1000],
                args_model=model, handler=handler)


async def tools_from_session(session: Any, allowlist: list[str]) -> list[Tool]:
    listed = await session.list_tools()
    tools: list[Tool] = []
    seen: set[str] = set()
    for t in listed.tools:
        if not allowed(t.name, allowlist):
            continue
        try:
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}  # mcp 2.x | 1.x
            tool = make_tool(session, t.name, t.description or "", schema)
        except UnsupportedSchema as e:
            log.warning("skipping MCP tool %s: %s", t.name, e)
            continue
        if tool.name in seen:
            log.warning("skipping MCP tool %s: name collision", t.name)
            continue
        seen.add(tool.name)
        tools.append(tool)
    return tools


async def _open_transport(stack: AsyncExitStack, url: str, headers: dict[str, str] | None):
    """Streamable HTTP transport; supports both the legacy (`streamablehttp_client(headers=)`) and the
    newer (`streamable_http_client(http_client=)`) mcp SDK APIs. Returns (read, write)."""
    from mcp.client import streamable_http as sh

    legacy = getattr(sh, "streamablehttp_client", None)
    if legacy is not None:
        streams = await stack.enter_async_context(legacy(url, headers=headers))
    else:
        from mcp.shared._httpx_utils import create_mcp_http_client

        client = await stack.enter_async_context(create_mcp_http_client(headers=headers))
        streams = await stack.enter_async_context(sh.streamable_http_client(url, http_client=client))
    return streams[0], streams[1]


async def _run_session(url: str, allowlist: list[str], ready: asyncio.Future, stop: asyncio.Event) -> None:
    """Owns the whole MCP connection lifecycle in ONE task (anyio cancel scopes require it)."""
    try:
        from mcp import ClientSession

        token = os.environ.get("MCP_GATEWAY_AUTH_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else None
        async with AsyncExitStack() as stack:
            read, write = await _open_transport(stack, url, headers)
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools = await tools_from_session(session, allowlist)
            log.info("MCP gateway: %d tools", len(tools))
            if not ready.done():
                ready.set_result(tools)
            await stop.wait()
    except asyncio.CancelledError:
        raise
    except BaseException as e:
        log.warning("MCP gateway unavailable (%s): continuing without MCP tools", e, exc_info=True)
    finally:
        if not ready.done():
            ready.set_result([])


@asynccontextmanager
async def mcp_tools():
    """Yield the MCP tools (possibly empty), keeping the session open meanwhile. Never raises on
    connection problems."""
    url = os.environ.get("MCP_GATEWAY_URL")
    if not url:
        yield []
        return
    allowlist = parse_allowlist(os.environ.get("MCP_TOOL_ALLOWLIST"))
    loop = asyncio.get_running_loop()
    ready: asyncio.Future = loop.create_future()
    stop = asyncio.Event()
    task = asyncio.create_task(_run_session(url, allowlist, ready, stop))
    try:
        tools = await ready
        yield tools
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except BaseException as e:
            if isinstance(e, asyncio.CancelledError) and not task.cancelled():
                raise
            log.warning("error closing MCP session: %s", e)
