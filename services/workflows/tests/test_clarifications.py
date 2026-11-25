"""Clarification answers: defaults, explicit picks and typed (custom) answers."""

from omnific_common.contracts import ClarifyAnswer, ClarifyAnswers, ClarifyQuestion
from omnific_workflows.simulation import build_clarifications

Q_ENV = ClarifyQuestion(id="environment", question="Where does it happen?", why="sets g", kind="choice",
                        options=["Lab bench", "The Moon"], unit="", default="Lab bench")
Q_H = ClarifyQuestion(id="height", question="Ledge height?", why="fall time", kind="number", options=[],
                      unit="meter", default="1")


def test_defaults_when_unanswered_or_skipped():
    out = build_clarifications([Q_ENV, Q_H], None)
    assert [(c.answer, c.used_default, c.custom) for c in out] == [("Lab bench", True, False), ("1", True, False)]
    skipped = build_clarifications([Q_ENV], ClarifyAnswers(answers=[ClarifyAnswer(id="environment", value="The Moon")],
                                                           use_defaults=True))
    assert skipped[0].used_default


def test_picked_option_keeps_the_question_unit():
    out = build_clarifications([Q_H], ClarifyAnswers(answers=[ClarifyAnswer(id="height", value="2.5")]))
    assert (out[0].answer, out[0].unit, out[0].used_default, out[0].custom) == ("2.5", "meter", False, False)


def test_custom_answers_are_explicit_and_keep_their_own_words_and_units():
    out = build_clarifications([Q_ENV, Q_H], ClarifyAnswers(answers=[
        ClarifyAnswer(id="environment", value="  On Mars, inside a dust storm ", custom=True),
        ClarifyAnswer(id="height", value="3 ft", custom=True),
    ]))
    assert (out[0].answer, out[0].custom, out[0].used_default) == ("On Mars, inside a dust storm", True, False)
    assert (out[1].answer, out[1].unit, out[1].custom) == ("3 ft", "", True)  # not "3 ft meter"


def test_blank_custom_answer_falls_back_to_the_default():
    out = build_clarifications([Q_H], ClarifyAnswers(answers=[ClarifyAnswer(id="height", value="  ", custom=True)]))
    assert (out[0].answer, out[0].used_default, out[0].custom) == ("1", True, False)
