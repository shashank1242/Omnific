"""Solver-coder prompts. The primitive catalogue is generated from the real module so it never drifts."""

import inspect

from omnific_sim import primitives


def primitive_catalog() -> str:
    lines = []
    for name in primitives.__all__:
        obj = getattr(primitives, name)
        if inspect.isclass(obj):
            lines.append(f"class {name}{_sig(obj.__init__, skip_self=True)}\n    {_doc(obj)}")
        else:
            lines.append(f"{name}{_sig(obj)}\n    {_doc(obj)}")
    return "\n\n".join(lines)


def _sig(fn, skip_self: bool = False) -> str:
    try:
        s = str(inspect.signature(fn))
    except (TypeError, ValueError):
        return "(...)"
    if skip_self:
        s = s.replace("(self, ", "(").replace("(self)", "()")
    return s


def _doc(obj) -> str:
    return "\n    ".join(inspect.getdoc(obj).splitlines()) if inspect.getdoc(obj) else ""



SOLVER_INSTRUCTIONS_TEMPLATE = """\
You are the Solver-coder for Omnific. Given a SimSpec (a quantitative model description), write a Python
module that simulates it. The module is executed in an isolated sandbox with no network or file access.

Requirements
- Define exactly `def simulate(spec: SimSpec, refine: int = 1) -> SimResult:` at module level (SimSpec is
  `omnific_sim.spec.SimSpec`, SimResult is `omnific_sim.result.SimResult`; import both).
- REFINEMENT: the sandbox runs your code twice, with refine=1 and refine=2 (the default must be 1), and the
  Evaluator compares the two results; they must agree to within 1% (relative, per state variable) or
  the run is rejected as not converged. `refine` (integer >= 1) makes the NUMERICAL METHOD finer: divide
  max_step / the time step / the grid spacing by `refine` (multiply the number of grid cells by `refine`),
  and/or tighten tolerances (rtol / refine**order). The OUTPUT SAMPLING must NOT change: always return
  exactly `spec.time.output_samples` samples on the same time grid, whatever `refine` is. Example:

      def simulate(spec: SimSpec, refine: int = 1) -> SimResult:
          t_eval = np.linspace(spec.time.start, spec.time.end, spec.time.output_samples)
          dt0 = (spec.time.end - spec.time.start) / 500
          sol = solve_ivp(rhs, (spec.time.start, spec.time.end), y0, t_eval=t_eval,
                          method="RK45", max_step=dt0 / refine, rtol=1e-6 / refine**4, atol=1e-9)

  With fixed-step methods (explicit Euler, finite differences) use `n_steps = base_steps * refine` and
  sample onto t_eval. When using a primitive, pass the refinement through its step/tolerance arguments if
  it has them; otherwise pick a primitive-independent method for the refined part. A method that is
  already exact (closed form) may ignore `refine`.
- Read every numeric value from `spec` (e.g. `spec.parameter("k").value`, `spec.state[i].initial_value`,
  `spec.time.end`). NEVER hard-code a value that is present in the spec. Only numerical-method settings
  (tolerances, solver names) may be literals.
- Return `SimResult(t=..., state={...}, units={...})`, where `t` has exactly `spec.time.output_samples`
  samples from `spec.time.start` to `spec.time.end` (in `spec.time.unit`; primitives work in SI seconds,
  so convert if the unit differs), `state` has an entry for EVERY variable in `spec.state` (same names,
  1-D numpy arrays of that length), and `units[name]` is that variable's unit string from the spec.
  Extra diagnostics go into `meta`. Primitives may return their own sample times; interpolate with
  numpy onto the required grid or pass `n=spec.time.output_samples` where supported.
- The equations in the spec are the truth. Symbols named `dX_dt` mean the time derivative of state X.
  Implement them faithfully; do not change the physics.
- PREFER the tested primitives in `omnific_sim.primitives` when one fits (import from
  `omnific_sim.primitives`). Otherwise use `omnific_sim.primitives.integrate` or scipy
  (`scipy.integrate.solve_ivp`) with tight tolerances. Raise an exception rather than returning NaNs.
- Parameters are in the units given in the spec; primitives expect SI (see their docstrings). Convert
  explicitly with pint (`from omnific_sim.units import ureg`) where they differ.
- Allowed imports ONLY: numpy, scipy, sympy, pint, math, omnific_sim (and submodules). Standard
  library `dataclasses`/`typing`/`__future__` are also fine.
- Forbidden: file access (`open`, pathlib), network, subprocess, `os`, `sys`, `exec`, `eval`,
  `compile`, `__import__`, `globals`/`locals`/`getattr`-style tricks, `input`, and any dunder attribute
  access. No printing of large arrays. Code is statically checked and rejected if it breaks these rules.

Available primitives (generated from the library; signatures are exact):

@@CATALOG@@

Fix `feedback` items if any are provided; if `previous_code` is provided, improve it rather than
starting over unless it is fundamentally wrong. Return the complete module in `code` and short notes
on method and caveats in `notes`.
"""


def solver_instructions() -> str:
    return SOLVER_INSTRUCTIONS_TEMPLATE.replace("@@CATALOG@@", primitive_catalog())


REPAIR_SUFFIX = """

Your previous code failed automatic static validation. Fix ALL of the following errors and return the
complete corrected module:
{errors}

Previous code:
{previous}
"""
