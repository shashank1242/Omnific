import itertools

import pytest
from omnific_librarian import chunking, figures
from omnific_librarian.chunking import Chunk, chunk_blocks, est_tokens
from omnific_librarian.parsers import Block

MAX = chunking.MAX_TOKENS


def para(i, n=300):
    return f"P{i} " + ("word " * (n // 5))


def doc_blocks():
    bl = [Block("heading", 1, "Intro", "Intro")]
    bl += [Block("text", 1 + i // 4, "Intro", para(i)) for i in range(20)]
    bl.append(Block("table", 3, "Intro", "| a | b |\n| --- | --- |\n" + "| 1 | 2 |\n" * 1200))
    bl.append(Block("figure", 3, "Intro", "A line chart of drag vs speed."))
    bl.append(Block("heading", 4, "Next", "Next"))
    bl += [Block("text", 4, "Next", para(100 + i)) for i in range(3)]
    return bl


def test_invariants():
    chunks = chunk_blocks(doc_blocks())
    assert [c.ord for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        if c.kind == "text":
            assert est_tokens(c.content) <= MAX
    big = [c for c in chunks if c.kind == "table"]
    assert len(big) == 1 and est_tokens(big[0].content) > MAX  # whole table kept
    assert big[0].content.count("| 1 | 2 |") == 1200
    assert [c.kind for c in chunks if c.kind != "text"] == ["table", "figure"]
    # sections do not mix
    nxt = [c for c in chunks if c.section == "Next"]
    assert len(nxt) == 1 and "P100" in nxt[0].content and "P102" in nxt[0].content
    assert all("P100" not in c.content for c in chunks if c.section == "Intro" and c.kind == "text")
    # order preserved
    seen = [int(p[1:]) for c in chunks if c.kind == "text" and c.section == "Intro"
            for p in c.content.split("\n\n") for p in [p.split()[0]]]
    firsts = []
    for s in seen:
        if not firsts or s != firsts[-1]:
            firsts.append(s)
    assert firsts == sorted(firsts) and firsts[0] == 0 and firsts[-1] == 19
    assert chunks[0].page == 1


def test_overlap_present():
    chunks = [c for c in chunk_blocks(doc_blocks()) if c.kind == "text" and c.section == "Intro"]
    assert len(chunks) >= 2
    for a, b in itertools.pairwise(chunks):
        last = a.content.split("\n\n")[-1]
        assert b.content.split("\n\n")[0] == last


def test_no_overlap_across_tables_and_headings():
    bl = [Block("text", 1, "S", "alpha para"), Block("table", 1, "S", "| t |"),
          Block("text", 1, "S", "beta para"), Block("heading", 1, "T", "T"),
          Block("text", 1, "T", "gamma para")]
    c = chunk_blocks(bl)
    assert [(x.kind, x.content) for x in c] == [
        ("text", "alpha para"), ("table", "| t |"), ("text", "beta para"), ("text", "gamma para")]


def test_long_paragraph_split():
    text = ". ".join(f"Sentence number {i} is here" for i in range(800)) + "."
    c = chunk_blocks([Block("text", 1, None, text)])
    assert len(c) > 1 and all(est_tokens(x.content) <= MAX for x in c)
    giant = chunk_blocks([Block("text", 1, None, "x" * 20000)])
    assert all(len(x.content) <= MAX * 4 for x in giant)
    assert chunk_blocks([Block("text", 1, None, "  "), Block("heading", 1, "H", "H")]) == []


class CtxLLM:
    def __init__(self, short=False, fail_title=False):
        self.calls = []
        self.short = short
        self.fail_title = fail_title

    async def structured(self, role, instructions, input, schema, tools=None):
        self.calls.append((role, input))
        if schema is chunking.Contexts:
            n = input.count("\n[") + (1 if input.startswith("[") else 0)
            n = sum(1 for line in input.splitlines() if line.startswith("[") and "kind=" in line)
            n = n - 1 if self.short else n
            return chunking.Contexts(contexts=[f"ctx{i}" for i in range(n)])
        if self.fail_title:
            raise RuntimeError("down")
        return chunking.TitleProposal(title="  A   Proposed Title ")


async def test_contexts_batched_and_ordered():
    from omnific_common.llm import Role

    chunks = [Chunk("text", 1, "S", f"c{i}") for i in range(23)]
    llm = CtxLLM()
    await chunking.add_contexts(chunks, "T", llm)
    assert len(llm.calls) == 3 and all(r is Role.FAST for r, _ in llm.calls)
    assert [c.context for c in chunks[:10]] == [f"ctx{i}" for i in range(10)]
    assert chunks[20].context == "ctx0" and chunks[22].context == "ctx2"


async def test_contexts_fallback_on_mismatch():
    chunks = [Chunk("text", 2, "Sec", "a"), Chunk("text", 2, None, "b")]
    await chunking.add_contexts(chunks, "Doc", CtxLLM(short=True))
    assert chunks[0].context == "ctx0"
    assert chunks[1].context == "From Doc (page 2)."


async def test_title():
    cs = [Chunk("text", 1, None, "hello")]
    assert await chunking.propose_title("f.txt", cs, CtxLLM()) == "A Proposed Title"
    assert await chunking.propose_title("f.txt", cs, CtxLLM(fail_title=True)) == "f.txt"


# --- figures ----------------------------------------------------------------------------------

class VisionLLM:
    def __init__(self, fail_on=None, reply="Line chart. Axes: t (s)."):
        self.n = 0
        self.fail_on = fail_on
        self.reply = reply
        self.prompts = []

    async def describe_image(self, role, prompt, image, mime):
        self.n += 1
        self.prompts.append((role, prompt, mime))
        if self.fail_on is not None and (self.fail_on == "all" or self.n in self.fail_on):
            raise RuntimeError("vision down")
        return self.reply


def fig_blocks(n):
    out = []
    for i in range(n):
        out.append(Block("text", i, "S", f"t{i}"))
        out.append(Block("figure", i, "S", None, b"img", "image/png"))
    return out


async def test_figure_cap_and_order():
    from omnific_common.llm import Role

    llm = VisionLLM()
    blocks, warns = await figures.describe_figures(fig_blocks(5), llm, limit=3)
    assert llm.n == 3
    assert [b.kind for b in blocks].count("figure") == 3
    assert [b.page for b in blocks if b.kind == "figure"] == [0, 1, 2]
    assert all(b.image is None and b.text.startswith("Line chart") for b in blocks if b.kind == "figure")
    assert len(warns) == 1 and "2 figure(s) skipped" in warns[0]
    role, prompt, mime = llm.prompts[0]
    assert role is Role.GENERAL and mime == "image/png"
    for needle in ("axes", "uncertain", "caption", "units"):
        assert needle in prompt.lower()


async def test_figure_cap_from_env(monkeypatch):
    monkeypatch.setenv("RAG_MAX_FIGURES", "1")
    llm = VisionLLM()
    await figures.describe_figures(fig_blocks(4), llm)
    assert llm.n == 1


async def test_figure_partial_failure_and_total_failure():
    blocks, warns = await figures.describe_figures(fig_blocks(3), VisionLLM(fail_on={2}), limit=10)
    assert [b.kind for b in blocks].count("figure") == 2 and len(warns) == 1
    with pytest.raises(RuntimeError):
        await figures.describe_figures(fig_blocks(3), VisionLLM(fail_on="all"), limit=10)


async def test_no_content_figures_dropped():
    blocks, _ = await figures.describe_figures(fig_blocks(2), VisionLLM(reply="NO_CONTENT"), limit=10)
    assert [b.kind for b in blocks] == ["text", "text"]
