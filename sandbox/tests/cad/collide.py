"""Fixture: the arm's range sweeps it through a wall standing next to it."""
from build123d import Box, Pos
from omnific_cad import part, revolute


def build():
    return {"base": part(Pos(0, 0.05, 0) * Box(0.3, 0.1, 0.3)),
            "wall": part(Pos(0.3, 0.3, 0) * Box(0.05, 0.6, 0.3)),
            "arm": part(Pos(0, 0.4, 0) * Box(0.06, 0.5, 0.06))}


JOINTS = [revolute("shoulder", "base", "arm", origin=(0, 0.15, 0), axis=(0, 0, 1), limits=(-90, 0))]
