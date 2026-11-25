"""Static guard for LLM-written Blender scripts (scene dressing). Runs inside the Blender runner before each script.

A seatbelt, not the isolation: the runner container (no network, read-only root, non-root, gVisor) is what makes
running the code safe. This only rejects obviously out-of-scope calls early, with a message the writer can act on.
Ported from the Astra Sim add-on (E:/Blender_mcp_test/astra_sim/safety.py). Stdlib only.
"""

from __future__ import annotations

import ast

ALLOWED_MODULES = {"bpy", "bmesh", "mathutils", "math", "random", "colorsys"}

BANNED_CALLS = {
    "open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "exit", "quit", "globals", "locals",
    "vars", "delattr", "setattr", "getattr",
}

# Dotted prefixes that must not appear anywhere in a script.
BANNED_PREFIXES = (
    "bpy.ops.wm.", "bpy.ops.script.", "bpy.ops.preferences.", "bpy.ops.extensions.", "bpy.ops.file.",
    "bpy.ops.render.", "bpy.ops.text.", "bpy.ops.sound.", "bpy.ops.import_scene.", "bpy.ops.export_scene.",
    "bpy.ops.image.", "bpy.utils.", "bpy.app.", "bpy.data.libraries", "bpy.data.texts", "bpy.context.preferences",
)

BANNED_ATTRS = {"as_module", "filepath", "filepath_raw", "save", "save_as_mainfile", "pack", "unpack", "reload",
                "driver_namespace", "handlers", "use_autopack", "script_directory"}


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def check(code: str) -> str | None:
    """None if the script may run, else why not."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno}): {(e.text or '').strip()}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    return f"import {alias.name!r} is not allowed (allowed: {', '.join(sorted(ALLOWED_MODULES))})"
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in ALLOWED_MODULES:
                return f"import from {node.module or '.'!r} is not allowed"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
            return f"{node.func.id}() is not allowed (line {node.lineno})"
        elif isinstance(node, ast.Name) and node.id.startswith("__"):
            return f"{node.id} is not allowed (line {node.lineno})"
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                return f".{node.attr} is not allowed (line {node.lineno})"
            if node.attr in BANNED_ATTRS:
                return f".{node.attr} is not allowed (line {node.lineno})"
            dotted = _dotted(node)
            if dotted and any((dotted + ".").startswith(p) or dotted.startswith(p) for p in BANNED_PREFIXES):
                return f"{dotted} is not allowed (line {node.lineno})"
    return None
