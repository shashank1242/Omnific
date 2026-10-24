"""Single shared pint registry. Every module must import `ureg` from here so quantities stay compatible."""

import pint

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity


def parse_quantity(value: float, unit: str) -> pint.Quantity:
    """Build a quantity, raising pint.errors.UndefinedUnitError on an unknown unit."""
    return Q_(value, unit)


def same_dimension(a: str, b: str) -> bool:
    return ureg.parse_units(a).dimensionality == ureg.parse_units(b).dimensionality
