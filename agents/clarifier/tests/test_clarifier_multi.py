"""A multi-select question: which results to show."""

from omnific_clarifier.validation import question_errors
from omnific_common.contracts import ClarifyQuestion


def q(default, options=("Fireball size", "Peak temperature", "Shock reach")):
    return ClarifyQuestion(id="outputs", question="Which results do you want to see?", why="headline numbers",
                           kind="multi", options=list(options), unit="", default=default)


def test_multi_default_is_one_or_more_options():
    assert question_errors(q("Fireball size; Peak temperature")) == []
    assert question_errors(q("Fireball size")) == []
    assert question_errors(q("Fireball size; Colour")) != []
    assert question_errors(q("Fireball size", options=["Fireball size"])) != []
