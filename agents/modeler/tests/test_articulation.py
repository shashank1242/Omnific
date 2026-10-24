import pytest
from omnific_modeler.prompts import MODEL_INSTRUCTIONS
from omnific_modeler.validation import validate_spec
from test_modeler import spec


def ents(*entities):
    return spec(entities=list(entities))


def test_pendulum_and_chain_valid():
    s = ents(
        {"name": "bob1", "kind": "sphere", "bound_state": ["T"], "pivot": [0, 2, 0], "arm": [0, -1, 0]},
        {"name": "bob2", "kind": "sphere", "parent": "bob1", "pivot": [0, 0, 0], "arm": [0, -1, 0]},
    )
    assert validate_spec(s) == []


@pytest.mark.parametrize("entities, frag", [
    ([{"name": "b", "kind": "sphere", "arm": [0, -1]}], "arm must be [x, y, z]"),
    ([{"name": "b", "kind": "sphere", "pivot": [0, 1, 0, 0], "arm": [0, -1, 0]}], "pivot must be [x, y, z]"),
    ([{"name": "b", "kind": "sphere", "parent": "ghost", "arm": [0, -1, 0]}], "not a declared entity"),
    ([{"name": "b", "kind": "sphere", "pivot": [0, 1, 0]}], "need an arm"),
    ([{"name": "a", "kind": "sphere", "parent": "b", "arm": [0, -1, 0]},
      {"name": "b", "kind": "sphere", "parent": "a", "arm": [0, -1, 0]}], "cycle"),
])
def test_invalid_articulation(entities, frag):
    errors = validate_spec(ents(*entities))
    assert any(frag in e for e in errors), errors


def test_prompt_explains_articulation():
    assert "arm" in MODEL_INSTRUCTIONS and "pivot" in MODEL_INSTRUCTIONS and "parent" in MODEL_INSTRUCTIONS
