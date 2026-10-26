"""Fixture: two unjoined parts sharing half their volume."""
from build123d import Box, Pos


def build():
    return {"a": Box(0.2, 0.2, 0.2), "b": Pos(0.1, 0, 0) * Box(0.2, 0.2, 0.2)}
