import contextlib

import pytest
from mcp.types import Tool as McpTool
from omnific_common.contracts import Clarification, Fact, ResearchInput, ResearchOutput
from omnific_rag import tools as doc_tools
from omnific_researcher import activities, mcp_tools
from omnific_sim.spec import Citation
from openai.lib._pydantic import to_strict_json_schema

CIT = Citation(title="NIST", url="https://webbook.nist.gov")


def fact(name, unit, cit=CIT):
    return Fact(name=name, value=1.0, unit=unit, description="d", citation=cit)


def doc_cit(url):
    return Citation(title="My Doc", url=url, note="quote")


class FakeLLM:
    def __init__(self, out):
        self.out, self.calls = out, []

    async def run_tools(self, role, instructions, input, schema, tools, hosted_tools=None, max_steps=8):
        self.calls.append({"role": role, "instructions": instructions, "input": input, "tools": tools,
                           "hosted": hosted_tools, "max_steps": max_steps})
        return self.out


def setup(monkeypatch, out, mcp=()):
    fake = FakeLLM(out)
    monkeypatch.setattr(activities, "llm", fake)

    @contextlib.asynccontextmanager
    async def fake_mcp():
        yield list(mcp)

    monkeypatch.setattr(activities, "mcp_tools", fake_mcp)
    return fake


def out_with(*facts):
    return ResearchOutput(summary="s", phenomena=["conduction"], facts=list(facts))


async def test_drops_bad_units(monkeypatch):
    fake = setup(monkeypatch, out_with(fact("cp", "joule/(kilogram*kelvin)"),
                                       fact("bad", "furlongs_per_fortnight_squared_x"), fact("dimless", "")))
    res = await activities.research(ResearchInput(job_id="j", prompt="p"))
    assert [f.name for f in res.facts] == ["cp", "dimless"]
    assert fake.calls[0]["role"].value == "MODEL_GENERAL" and fake.calls[0]["hosted"]
    assert fake.calls[0]["max_steps"] == 8


async def test_max_steps_env(monkeypatch):
    monkeypatch.setenv("RESEARCH_MAX_STEPS", "3")
    fake = setup(monkeypatch, out_with())
    await activities.research(ResearchInput(job_id="j", prompt="p"))
    assert fake.calls[0]["max_steps"] == 3


async def test_doc_tools_only_with_document_ids(monkeypatch):
    fake = setup(monkeypatch, out_with())
    await activities.research(ResearchInput(job_id="j", prompt="p"))
    assert fake.calls[0]["tools"] == []
    await activities.research(ResearchInput(job_id="j", prompt="p", document_ids=["d1"]))
    assert [t.name for t in fake.calls[1]["tools"]] == ["search_documents", "read_chunk_neighbors"]
    for t in fake.calls[1]["tools"]:
        assert t.spec()["strict"]


async def test_clarifications_in_input(monkeypatch):
    fake = setup(monkeypatch, out_with())
    cl = [Clarification(question="Altitude?", answer="100", unit="m", used_default=False)]
    await activities.research(ResearchInput(job_id="j", prompt="p", clarifications=cl, document_ids=["d1"]))
    assert "Altitude?" in fake.calls[0]["input"] and "binding" in fake.calls[0]["input"]
    assert "d1" in fake.calls[0]["input"]


async def test_citation_format(monkeypatch):
    good = fact("a", "kelvin", doc_cit("omnific://doc/d1#page=3"))
    nopage = fact("b", "kelvin", doc_cit("omnific://doc/d1"))
    unknown = fact("c", "kelvin", doc_cit("omnific://doc/zzz#page=1"))
    web = fact("d", "kelvin")
    setup(monkeypatch, out_with(good, nopage, unknown, web))
    res = await activities.research(ResearchInput(job_id="j", prompt="p", document_ids=["d1"]))
    assert [f.name for f in res.facts] == ["a", "b", "d"]
    assert len(res.open_questions) == 2
    assert any("'b'" in q and "page" in q for q in res.open_questions)
    assert any("'c'" in q and "dropped" in q for q in res.open_questions)


async def test_doc_tool_handlers(monkeypatch):
    seen = {}

    async def fake_search(query, document_ids, kinds):
        seen["s"] = (query, document_ids, kinds)
        return [{"document_id": "d1", "page": 2, "content": "x" * 5000}]

    async def fake_neighbors(document_id, ord):
        return [{"document_id": document_id, "ord": ord}]

    monkeypatch.setattr(doc_tools, "rag_search", fake_search)
    monkeypatch.setattr(doc_tools, "rag_neighbors", fake_neighbors)
    search, nb = doc_tools.document_tools(["d1"])
    r = await search.handler(search.args_model(query="q", kinds=["table"]))
    assert seen["s"] == ("q", ["d1"], ["table"]) and len(r["hits"][0]["content"]) < 2100
    assert (await nb.handler(nb.args_model(document_id="d1", ord=4)))["chunks"][0]["ord"] == 4
    assert "error" in await nb.handler(nb.args_model(document_id="other", ord=1))


def test_strict_schema():
    assert to_strict_json_schema(ResearchOutput)


# --- MCP ---------------------------------------------------------------------------------------

SCHEMAS = {
    "read_file": {"type": "object", "properties": {
        "path": {"type": "string", "description": "file"},
        "encoding": {"type": "string", "enum": ["utf-8", "latin-1"]},
        "limit": {"type": "integer"}}, "required": ["path"]},
    "search-repos": {"type": "object", "properties": {
        "query": {"type": "string"},
        "filters": {"type": "object", "properties": {
            "language": {"type": "string"}, "stars": {"type": "number"},
            "topics": {"type": "array", "items": {"type": "string"}}}, "required": ["language"]},
        "sort": {"anyOf": [{"type": "string", "enum": ["stars", "updated"]}, {"type": "null"}]},
        "include_forks": {"type": "boolean", "default": False}}, "required": ["query"],
        "additionalProperties": False},
    "refs": {"type": "object", "properties": {"item": {"$ref": "#/$defs/Item"},
                                              "items": {"type": "array", "items": {"$ref": "#/$defs/Item"}}},
             "$defs": {"Item": {"type": "object", "properties": {"id": {"type": "string"},
                                                                 "_meta": {"type": ["string", "null"]}}}}},
    "no_args": {"type": "object", "properties": {}},
    "weird.name/with spaces": {"type": "object", "properties": {"x-y": {"type": "string"}}},
}


def test_schema_conversion_strict():
    for name, schema in SCHEMAS.items():
        model = mcp_tools.args_model_for(name, schema)
        js = to_strict_json_schema(model)
        assert js["additionalProperties"] is False
        assert set(js.get("required", [])) == set(js.get("properties", {}))


def test_schema_roundtrip_alias_and_none_dropped():
    m = mcp_tools.args_model_for("weird", SCHEMAS["weird.name/with spaces"])
    a = m.model_validate_json('{"x-y": "v"}')
    assert a.model_dump(by_alias=True, exclude_none=True) == {"x-y": "v"}
    m = mcp_tools.args_model_for("s", SCHEMAS["search-repos"])
    a = m.model_validate_json('{"query":"q","filters":null,"sort":"stars","include_forks":null}')
    assert a.model_dump(by_alias=True, exclude_none=True) == {"query": "q", "sort": "stars"}


def test_unsupported_schema():
    for bad in ({"type": "object", "properties": {"blob": {"type": "object"}}},
                {"type": "object", "properties": {"u": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}):
        with pytest.raises(mcp_tools.UnsupportedSchema):
            mcp_tools.args_model_for("t", bad)


def test_tool_name_and_allowlist():
    n = mcp_tools.tool_name("weird.name/with spaces" + "x" * 100)
    assert len(n) == 64 and n.startswith("mcp__") and "." not in n and " " not in n
    gl = mcp_tools.parse_allowlist("read_*, search-?epos")
    assert mcp_tools.allowed("read_file", gl) and mcp_tools.allowed("search-repos", gl)
    assert not mcp_tools.allowed("write_file", gl)
    assert mcp_tools.parse_allowlist(None) == ["*"]


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeSession:
    def __init__(self):
        self.called = []

    async def list_tools(self):
        # real SDK objects, so a field rename in mcp (inputSchema -> input_schema in 2.x) breaks this test
        tools = [McpTool.model_validate({"name": k, "description": "d", "inputSchema": v})
                 for k, v in SCHEMAS.items()]
        tools.append(McpTool.model_validate({"name": "bad", "description": "", "inputSchema": {
            "type": "object", "properties": {"o": {"type": "object"}}}}))
        return Obj(tools=tools)

    async def call_tool(self, name, args):
        self.called.append((name, args))
        return Obj(content=[Obj(type="text", text="y" * 20000)], isError=False)


async def test_tools_from_session_allowlist_and_call():
    s = FakeSession()
    tools = await mcp_tools.tools_from_session(s, ["*"])
    names = [t.name for t in tools]
    assert "mcp__read_file" in names and "mcp__bad" not in names and len(tools) == len(SCHEMAS)
    only = await mcp_tools.tools_from_session(s, ["read_*"])
    assert [t.name for t in only] == ["mcp__read_file"]
    t = only[0]
    out = await t.handler(t.args_model.model_validate_json('{"path":"a","encoding":null,"limit":null}'))
    assert s.called == [("read_file", {"path": "a"})] and len(out) < 8300 and out.endswith("[truncated]")


async def test_mcp_unset_and_unavailable(monkeypatch):
    monkeypatch.delenv("MCP_GATEWAY_URL", raising=False)
    async with mcp_tools.mcp_tools() as t:
        assert t == []
    monkeypatch.setenv("MCP_GATEWAY_URL", "http://127.0.0.1:1/mcp")
    async with mcp_tools.mcp_tools() as t:
        assert t == []


async def test_research_works_when_mcp_down(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_URL", "http://127.0.0.1:1/mcp")
    fake = FakeLLM(out_with(fact("cp", "")))
    monkeypatch.setattr(activities, "llm", fake)
    res = await activities.research(ResearchInput(job_id="j", prompt="p"))
    assert len(res.facts) == 1 and fake.calls[0]["tools"] == []


async def test_auth_header_legacy_api(monkeypatch):
    import mcp.client.streamable_http as sh

    seen = []

    @contextlib.asynccontextmanager
    async def fake_client(url, headers=None, **kw):
        seen.append((url, headers))
        raise ConnectionError("stop here")
        yield  # pragma: no cover

    monkeypatch.setattr(sh, "streamablehttp_client", fake_client, raising=False)
    monkeypatch.setenv("MCP_GATEWAY_URL", "http://mcp-gateway:8811/mcp")
    monkeypatch.setenv("MCP_GATEWAY_AUTH_TOKEN", "tok")
    async with mcp_tools.mcp_tools() as t:
        assert t == []
    monkeypatch.delenv("MCP_GATEWAY_AUTH_TOKEN")
    async with mcp_tools.mcp_tools() as t:
        assert t == []
    assert seen == [("http://mcp-gateway:8811/mcp", {"Authorization": "Bearer tok"}),
                    ("http://mcp-gateway:8811/mcp", None)]


async def test_auth_header_new_api(monkeypatch):
    import mcp.client.streamable_http as sh
    import mcp.shared._httpx_utils as hu

    monkeypatch.delattr(sh, "streamablehttp_client", raising=False)
    seen = []

    @contextlib.asynccontextmanager
    async def fake_http(headers=None, **kw):
        seen.append(headers)
        yield "client"

    @contextlib.asynccontextmanager
    async def fake_client(url, http_client=None, **kw):
        assert http_client == "client"
        raise ConnectionError("stop here")
        yield  # pragma: no cover

    monkeypatch.setattr(hu, "create_mcp_http_client", fake_http)
    monkeypatch.setattr(sh, "streamable_http_client", fake_client, raising=False)
    monkeypatch.setenv("MCP_GATEWAY_URL", "http://mcp-gateway:8811/mcp")
    monkeypatch.setenv("MCP_GATEWAY_AUTH_TOKEN", "tok")
    async with mcp_tools.mcp_tools() as t:
        assert t == []
    monkeypatch.delenv("MCP_GATEWAY_AUTH_TOKEN")
    async with mcp_tools.mcp_tools() as t:
        assert t == []
    assert seen == [{"Authorization": "Bearer tok"}, None]


def test_denylist_beats_allowlist():
    assert not mcp_tools.allowed("download_scihub", ["*"])
    assert not mcp_tools.allowed("Download_SciHub", ["download_*"])
    assert mcp_tools.allowed("search_arxiv", ["*"])
