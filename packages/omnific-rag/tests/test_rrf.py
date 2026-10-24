import pytest
from omnific_rag import search
from omnific_rag.models import citation_url


def test_rrf_scores_exact():
    fused = dict(search.rrf_fuse([[1, 2, 3], [3, 1]], k=60))
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[2] == pytest.approx(1 / 62)
    assert fused[3] == pytest.approx(1 / 63 + 1 / 61)


def test_rrf_order_agreement_wins():
    out = [i for i, _ in search.rrf_fuse([[1, 2, 3], [3, 1]])]
    assert out == [1, 3, 2]


def test_rrf_empty_and_single():
    assert search.rrf_fuse([]) == []
    assert search.rrf_fuse([[], []]) == []
    assert [i for i, _ in search.rrf_fuse([[5, 4, 6]])] == [5, 4, 6]


def test_rrf_default_k_is_60():
    assert search.rrf_fuse([[7]])[0][1] == pytest.approx(1 / 61)


async def test_search_refuses_empty_scope():
    assert await search.search("anything", []) == []
    assert await search.search("anything", ()) == []


def test_citation_url():
    assert citation_url("d1", 3) == "omnific://doc/d1#page=3"
    assert citation_url("d1", None) == "omnific://doc/d1"
