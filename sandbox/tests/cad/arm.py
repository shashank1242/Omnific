"""Fixture: a base with an arm on a revolute joint that oscillates within limits clear of the base."""
from build123d import Box, Pos
from omnific_cad import part, revolute


def build():
    return {"base": part(Pos(0, 0.05, 0) * Box(0.3, 0.1, 0.3), color="#555555"),
            "arm": part(Pos(0, 0.4, 0) * Box(0.06, 0.5, 0.06), color="#e67e22")}


JOINTS = [revolute("shoulder", "base", "arm", origin=(0, 0.15, 0), axis=(0, 0, 1), limits=(-30, 30),
                   motion="oscillate", clip="wave")]
