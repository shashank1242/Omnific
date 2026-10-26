"""Fixture: a cart (body + 4 wheels that spin about x). Metres, +Y up, front = +Z."""
from build123d import Box, Cylinder, Pos, Rot
from omnific_cad import part, revolute

L, W, H, R, T = 0.6, 0.36, 0.12, 0.08, 0.04
WHEELS = {"wheel_fl": (W / 2 + T / 2 + 0.005, R, L / 3), "wheel_fr": (-W / 2 - T / 2 - 0.005, R, L / 3),
          "wheel_rl": (W / 2 + T / 2 + 0.005, R, -L / 3), "wheel_rr": (-W / 2 - T / 2 - 0.005, R, -L / 3)}


def build():
    parts = {"body": part(Pos(0, R + H / 2 + 0.02, 0) * Box(W, H, L), color="#c0392b")}
    for name, (x, y, z) in WHEELS.items():
        parts[name] = part(Pos(x, y, z) * Rot(0, 90, 0) * Cylinder(R, T), color="#222222")
    return parts


JOINTS = [revolute(f"spin_{n}", "body", n, origin=xyz, axis=(1, 0, 0), motion="spin", clip="roll")
          for n, xyz in WHEELS.items()]
