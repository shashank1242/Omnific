"""Static AST validation of generated solver code. Defense in depth; the sandbox is the real isolation."""

import ast

ALLOWED_IMPORT_ROOTS = frozenset({
    "numpy", "scipy", "sympy", "pint", "omnific_sim", "math", "typing", "dataclasses", "__future__",
})
BANNED_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "memoryview", "exit", "quit", "help",
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "ctypes", "importlib", "builtins",
    "requests", "urllib", "http", "pickle", "marshal", "multiprocessing", "threading", "signal",
})


def _check_refine(args: ast.arguments) -> list[str]:
    pos = args.posonlyargs + args.args
    default_of = {a.arg: d for a, d in zip(pos[len(pos) - len(args.defaults):], args.defaults, strict=True)}
    default_of.update({a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                       if d is not None})
    if "refine" not in {a.arg for a in pos[1:] + args.kwonlyargs}:
        return ["simulate must accept a `refine` parameter: `def simulate(spec, refine=1)`"]
    d = default_of.get("refine")
    if not (isinstance(d, ast.Constant) and d.value == 1 and type(d.value) is int):
        return ["the `refine` parameter of simulate must have default 1"]
    return []


def validate_code(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e.msg} (line {e.lineno})"]
    errors: list[str] = []
    has_simulate = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                    errors.append(f"line {node.lineno}: import of {a.name!r} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                errors.append(f"line {node.lineno}: import from {node.module!r} is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in BANNED_NAMES:
                errors.append(f"line {node.lineno}: use of banned name {node.id!r}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                errors.append(f"line {node.lineno}: dunder attribute {node.attr!r} is not allowed")
            elif node.attr in BANNED_NAMES - {"help"}:
                errors.append(f"line {node.lineno}: use of banned attribute {node.attr!r}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in BANNED_NAMES:
            # e.g. getattr-free tricks like __builtins__["open"]; only flag exact matches
            errors.append(f"line {node.lineno}: string literal {node.value!r} matches a banned name")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "simulate":
            has_simulate = True
            args = node.args
            if len(args.posonlyargs) + len(args.args) < 1:
                errors.append("simulate must accept the spec as its first argument")
            errors += _check_refine(args)
    if not has_simulate:
        errors.append("module must define a top-level function `simulate(spec, refine=1)`")
    return errors
