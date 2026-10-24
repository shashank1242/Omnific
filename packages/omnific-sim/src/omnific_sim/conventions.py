"""SimSpec equation conventions shared by the Modeler (validation) and the Evaluator (checks).

- Equations are 'lhs = rhs', SymPy-parsable, over declared parameter/state names and 't'.
- The time derivative of state X is the symbol `dX_dt`, with unit unit(X)/unit(time).
- `pi` and `E` are numeric constants unless declared as names.
- Equations using functions in SKIP_DIMENSION_CHECK are not dimension-checked (dimension_check can't
  reason about them).
"""

import re

import sympy as sp
from sympy.parsing.sympy_parser import parse_expr

from .primitives.checks import dimension_check
from .spec import SimSpec

ALLOWED_FUNCTIONS = frozenset({
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh", "exp", "log",
    "sqrt", "Abs", "Min", "Max", "Piecewise", "sign", "floor", "ceiling",
})
ALLOWED_CONSTANTS = frozenset({"pi", "E"})
SKIP_DIMENSION_CHECK = frozenset({"Abs", "Min", "Max", "Piecewise", "sign", "floor", "ceiling", "atan2"})

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d+\.?\d*(?:[eE][+-]?\d+)?")
_CONST_VALUES = {"pi": "3.141592653589793", "E": "2.718281828459045"}


def derivative_symbol(state_name: str) -> str:
    return f"d{state_name}_dt"


def identifiers(expr: str) -> set[str]:
    # strip numeric literals first so '1e-3' does not yield the identifier 'e'
    return set(_IDENT.findall(_NUMBER.sub(" ", expr)))


def symbol_units(spec: SimSpec) -> dict[str, str]:
    """Unit of every symbol an equation may use: parameters, states, 't' and each dX_dt."""
    units = {p.name: p.unit or "dimensionless" for p in spec.parameters}
    units.update({s.name: s.unit or "dimensionless" for s in spec.state})
    units["t"] = spec.time.unit
    for s in spec.state:
        units[derivative_symbol(s.name)] = f"({s.unit or 'dimensionless'})/({spec.time.unit})"
    return units


def check_equation_dimensions(expression: str, units: dict[str, str]) -> bool | None:
    """True/False for consistent/inconsistent, None if the equation is exempt from checking.

    Raises KeyError for unknown symbols and other exceptions for unparsable expressions.
    """
    ids = identifiers(expression)
    if ids & SKIP_DIMENSION_CHECK:
        return None
    # Zero carries any dimension, so 'dX_dt = 0' is consistent whatever X's unit is.
    local = {n: sp.Symbol(n) for n in ids - ALLOWED_FUNCTIONS - ALLOWED_CONSTANTS}
    if any(parse_expr(side, local_dict=local).is_zero for side in expression.split("=", 1)):
        return True
    expr = expression
    for const in ALLOWED_CONSTANTS & ids:
        if const not in units:
            expr = re.sub(rf"\b{const}\b", _CONST_VALUES[const], expr)
    used = {k: v for k, v in units.items() if k in ids}
    return dimension_check(expr, used)
