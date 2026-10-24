import pytest
from omnific_librarian import parsers
from omnific_librarian.parsers import CorruptFile, UnsupportedFile, markdown_table, parse

from . import fixtures as fx


def kinds(p):
    return [b.kind for b in p.blocks]


def texts(p, kind):
    return [b.text for b in p.blocks if b.kind == kind]


def test_markdown_table():
    md = markdown_table([["a", "b|c"], ["1"], [None, "x\ny"]])
    assert md.splitlines()[0] == "| a | b\\|c |"
    assert md.splitlines()[1].count("---") == 2
    assert "| 1 |  |" in md and "| x y" in md
    assert markdown_table([[None, ""]]) == ""
    capped = markdown_table([["h"]] + [[str(i)] for i in range(10)], max_rows=3)
    assert "truncated to 3 rows" in capped and "| 2 |" in capped and "| 3 |" not in capped


def test_pdf():
    p = parse(fx.make_pdf(), "r.pdf", "application/pdf")
    assert p.pages == 2
    heads = [(b.text, b.page) for b in p.blocks if b.kind == "heading"]
    assert heads == [("Wind Tunnel Report", 1), ("2. Method", 1), ("3. Results", 2)]
    assert p.title == "Wind Tunnel Report"
    body = [b for b in p.blocks if b.kind == "text"]
    assert any("scale model" in b.text and b.section == "Wind Tunnel Report" for b in body)
    assert any("force balance" in b.text and b.section == "2. Method" for b in body)
    tables = [b for b in p.blocks if b.kind == "table"]
    assert len(tables) == 1 and tables[0].page == 1 and tables[0].section == "2. Method"
    assert "| Speed | Drag | Unit |" in tables[0].text and "| 20 | 6.1 | N |" in tables[0].text
    # table cell text must not leak into body paragraphs
    assert not any("6.1" in b.text for b in body)
    figs = [b for b in p.blocks if b.kind == "figure"]
    assert len(figs) == 1 and figs[0].page == 2 and figs[0].mime == "image/png"
    assert figs[0].image.startswith(b"\x89PNG")
    # ordering: heading, text, ..., table before page 2 content
    order = kinds(p)
    assert order.index("table") < order.index("figure")


def test_docx():
    p = parse(fx.make_docx(), "n.docx")
    assert p.title == "Hull Design Notes"
    assert [b.text for b in p.blocks if b.kind == "heading"] == ["Hull Design Notes", "Materials", "Loads"]
    t = texts(p, "table")[0]
    assert "| Part | Mass kg |" in t and "| Keel | 40 |" in t
    assert any(b.kind == "text" and "foam core" in b.text and b.section == "Materials" for b in p.blocks)
    fig = [b for b in p.blocks if b.kind == "figure"]
    assert len(fig) == 1 and fig[0].section == "Loads" and fig[0].mime == "image/png"
    # document order: table comes before the Loads heading
    assert kinds(p).index("table") < [b.text for b in p.blocks].index("Loads")


def test_pptx():
    p = parse(fx.make_pptx(), "s.pptx")
    assert p.pages == 2
    assert [(b.text, b.page) for b in p.blocks if b.kind == "heading"] == [
        ("Launch Overview", 1), ("Budget", 2)]
    assert any("50 kN" in b.text and b.page == 1 for b in p.blocks if b.kind == "text")
    assert "| Item | Cost |" in texts(p, "table")[0]
    fig = [b for b in p.blocks if b.kind == "figure"]
    assert len(fig) == 1 and fig[0].page == 2 and fig[0].image


def test_xlsx_and_csv():
    p = parse(fx.make_xlsx(), "m.xlsx")
    assert p.pages == 2
    t = texts(p, "table")
    assert "| Steel | 7850 |" in t[0] and len(t) == 1  # empty sheet yields no table
    assert [b.text for b in p.blocks if b.kind == "heading"] == ["Sheet: Materials", "Sheet: Empty"]
    c = parse(fx.make_csv(), "d.csv", "text/csv")
    assert c.blocks[0].kind == "table" and "| 2 | 4 |" in c.blocks[0].text


def test_csv_row_cap(monkeypatch):
    monkeypatch.setattr(parsers, "MAX_TABLE_ROWS", 5)
    c = parse(fx.make_csv(100), "d.csv")
    assert "truncated to 5 rows" in c.blocks[0].text and "| 4 | 16 |" in c.blocks[0].text
    assert "| 5 | 25 |" not in c.blocks[0].text


def test_html():
    p = parse(fx.HTML, "p.html")
    assert p.title == "Fluid Page"
    assert [b.text for b in p.blocks if b.kind == "heading"] == ["Fluids", "Table"]
    assert "var x" not in " ".join(b.text for b in p.blocks)
    assert "| Water | 1000 |" in texts(p, "table")[0]
    assert "Water is incompressible." in texts(p, "text")
    assert "First item" in texts(p, "text")


def test_markdown_and_txt():
    p = parse(fx.MD, "n.md")
    assert p.title == "Notes"
    assert kinds(p) == ["heading", "text", "heading", "table", "text"]
    assert p.blocks[3].text.startswith("| a | b |") and p.blocks[3].section == "Data"
    t = parse(b"One para.\n\nTwo para.\n", "n.txt", "text/plain")
    assert texts(t, "text") == ["One para.", "Two para."]


def test_image():
    p = parse(fx.make_png(), "x.png")
    assert kinds(p) == ["figure"] and p.blocks[0].mime == "image/png" and p.blocks[0].page == 1


def test_errors():
    with pytest.raises(UnsupportedFile):
        parse(b"x", "a.exe", "application/octet-stream")
    with pytest.raises(CorruptFile):
        parse(b"not a pdf", "a.pdf")
    with pytest.raises(CorruptFile):
        parse(b"not a zip", "a.docx")
    with pytest.raises(CorruptFile):
        parse(b"not an image", "a.png")


def test_detect_by_content_type():
    assert parsers.detect_format("noext", "text/plain; charset=utf-8") == "txt"
