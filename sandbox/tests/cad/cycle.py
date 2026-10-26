"""Fixture: a joint graph that is not a tree."""
from build123d import Box, Pos
from omnific_cad import revolute


def build():
    return {"a": Box(0.1, 0.1, 0.1), "b": Pos(0.3, 0, 0) * Box(0.1, 0.1, 0.1)}


JOINTS = [revolute("j1", "a", "b", origin=(0.2, 0, 0), axis=(0, 0, 1)),
          revolute("j2", "b", "a", origin=(0.2, 0, 0), axis=(0, 0, 1))]
