"""A crashed job says why when the user can act on it, and nothing internal otherwise."""

from omnific_api.app import failure_message


class Wrapped(Exception):
    def __init__(self, msg, cause=None):
        super().__init__(msg)
        self.cause = cause


def test_out_of_credits_is_explained():
    inner = Exception("Error code: 429 - {'error': {'code': 'credit_balance_exhausted', 'type': 'insufficient_quota'}}")
    msg = failure_message(Wrapped("Workflow execution failed", Wrapped("Activity task failed", inner)))
    assert "run out of credits" in msg


def test_other_failures_stay_generic():
    assert failure_message(Wrapped("Workflow execution failed", Exception("KeyError: 'x' in solver.py line 3"))) == ""
    assert failure_message(None) == ""
