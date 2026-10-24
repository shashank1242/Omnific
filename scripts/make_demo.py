"""Generate offline demo bundles (no LLM): hand-written SimSpecs run through the real primitives,
the Data agent's build_playback, the Mesh agent's glTF builder and the Evaluator's deterministic checks.

Usage (repo root): uv run --no-sync python scripts/make_demo.py
Writes apps/web/public/demo/<slug>/{playback.json,scene.gltf,outcome.json} and demo/index.json.
"""

import asyncio
import json
import os
from pathlib import Path

os.environ["EVAL_CRITIC"] = "off"

import numpy as np
from omnific_common.contracts import (
    EvalInput,
    EvalReport,
    Fact,
    JobOutcome,
    ResearchOutput,
    SolverCode,
)
from omnific_data.activities import build_playback, dumps
from omnific_evaluator import activities as ev
from omnific_mesh.assets import asset_for_kind
from omnific_mesh.gltf import build_gltf
from omnific_mesh.gltf import dumps as gltf_dumps
from omnific_sim.primitives import heat_1d, lumped_capacitance, pendulum, projectile
from omnific_sim.primitives.ode import integrate
from omnific_sim.result import SimResult
from omnific_sim.spec import (
    AnalyticCheck,
    Bound,
    Citation,
    Domain,
    Entity,
    Equation,
    Fidelity,
    Invariant,
    Parameter,
    SimSpec,
    StateVariable,
    TimeSpan,
)

OUT = Path(__file__).resolve().parent.parent / "apps" / "web" / "public" / "demo"

NASA_VENUS = Citation(title="NASA NSSDC Venus Fact Sheet",
                      url="https://nssdc.gsfc.nasa.gov/planetary/factsheet/venusfact.html")
NASA_MARS = Citation(title="NASA NSSDC Mars Fact Sheet",
                     url="https://nssdc.gsfc.nasa.gov/planetary/factsheet/marsfact.html")
NIST_CODATA = Citation(title="NIST CODATA: Stefan-Boltzmann constant",
                       url="https://physics.nist.gov/cgi-bin/cuu/Value?sigma")
WIKI_PENDULUM = Citation(title="Pendulum (mechanics) - Wikipedia",
                         url="https://en.wikipedia.org/wiki/Pendulum_(mechanics)")


def P(name, value, unit, desc="", source=None):
    return Parameter(name=name, value=value, unit=unit, description=desc, source=source)


def S(name, unit, init, desc=""):
    return StateVariable(name=name, unit=unit, initial_value=init, description=desc)


def projectile_demo():
    g, v0, ang = 3.72076, 40.0, 45.0
    res = projectile(v0, ang, g=g, n=200)
    fine = projectile(v0, ang, g=g, n=200, rtol=1e-12, atol=1e-14)
    spec = SimSpec(
        title="Projectile on Mars", prompt="Launch a ball at 40 m/s and 45 degrees on Mars.",
        domains=[Domain.MECHANICS], fidelity=Fidelity.ANALYTIC,
        assumptions=["No atmosphere drag (Mars surface pressure is ~0.6% of Earth's)",
                     "Flat terrain; uniform gravity; vx is constant (no horizontal force)", "Ball is a point mass"],
        parameters=[P("g", g, "meter/second**2", "Martian surface gravity", NASA_MARS),
                    P("v0", v0, "meter/second", "Launch speed"),
                    P("angle", ang, "degree", "Launch angle above horizontal")],
        state=[S("x", "meter", 0), S("y", "meter", 0),
               S("vx", "meter/second", v0 * np.cos(np.radians(ang))),
               S("vy", "meter/second", v0 * np.sin(np.radians(ang)))],
        equations=[Equation(label="x", expression="dx_dt = vx"),
                   Equation(label="y", expression="dy_dt = vy"),
                   Equation(label="vy", expression="dvy_dt = -g")],
        entities=[Entity(name="ball", kind="sphere", bound_state=["x", "y"])],
        time=TimeSpan(end=float(res.t[-1]), unit="second", output_samples=200),
        invariants=[Invariant(name="specific_energy", expression="0.5*(vx**2+vy**2)+g*y",
                              rel_tolerance=1e-6)],
        bounds=[Bound(variable="y", min=-1e-6)],
        analytic_checks=[AnalyticCheck(variable="y", expression="v0*sin(angle*pi/180)*t - g*t**2/2",
                                       rel_tolerance=1e-6, description="Vacuum parabola")],
        citations=[NASA_MARS],
    )
    research = ResearchOutput(
        summary=("Mars surface gravity is 3.71 m/s^2 (3.72076 at the equator), about 38% of Earth's. The "
                 "atmosphere is ~0.6% of Earth's pressure, so drag on a dense ball is negligible and the "
                 "trajectory is a vacuum parabola: range = v0^2 sin(2a)/g and apex = (v0 sin a)^2/(2g)."),
        phenomena=["ballistic motion", "uniform gravity"],
        facts=[Fact(name="g_mars", value=g, unit="m/s^2", description="Mars equatorial surface gravity",
                    citation=NASA_MARS)])
    code = "def simulate(spec, refine=1):\n    ...  # omnific_sim.primitives.projectile\n"
    return "projectile-mars", spec, res, fine, research, code, None


def pendulum_demo():
    L, g, th0 = 1.0, 9.80665, 1.2
    res = pendulum(L, g, th0, 10.0, 400)
    fine = integrate(lambda t, y: {"theta": y["omega"], "omega": -(g / L) * np.sin(y["theta"])},
                     {"theta": th0, "omega": 0.0}, {"theta": "radian", "omega": "radian/second"},
                     (0.0, 10.0), 400, rtol=1e-12, atol=1e-14)
    spec = SimSpec(
        title="Nonlinear pendulum", prompt="Simulate a 1 m pendulum released from 69 degrees (1.2 rad).",
        domains=[Domain.MECHANICS], fidelity=Fidelity.VALIDATED_NUMERICAL,
        assumptions=["Massless rigid rod, point bob", "No friction or air drag", "Full nonlinear sin(theta)"],
        parameters=[P("L", L, "meter", "Rod length"),
                    P("g", g, "meter/second**2", "Standard gravity")],
        state=[S("theta", "radian", th0, "Angle from vertical"), S("omega", "radian/second", 0.0)],
        equations=[Equation(label="theta", expression="dtheta_dt = omega"),
                   Equation(label="omega", expression="domega_dt = -g/L*sin(theta)")],
        entities=[Entity(name="pendulum", kind="rod", bound_state=["theta"])],
        time=TimeSpan(end=10.0, unit="second", output_samples=400),
        invariants=[Invariant(name="specific_energy",
                              expression="0.5*L**2*omega**2 + g*L*(1-cos(theta))", rel_tolerance=1e-6)],
        bounds=[Bound(variable="theta", min=-1.2001, max=1.2001)],
        citations=[WIKI_PENDULUM],
    )
    research = ResearchOutput(
        summary=("For small amplitudes the period is 2*pi*sqrt(L/g) (~2.006 s for 1 m). At 1.2 rad the "
                 "exact period is longer by a factor 2K(sin(theta0/2))/pi, roughly 1.17, so the small-angle "
                 "approximation is not used; total mechanical energy is the conserved check."),
        phenomena=["nonlinear oscillation", "energy conservation"],
        facts=[Fact(name="g", value=g, unit="m/s^2", description="Standard gravity",
                    citation=WIKI_PENDULUM)])
    code = "def simulate(spec, refine=1):\n    ...  # omnific_sim.primitives.pendulum\n"
    return "pendulum", spec, res, fine, research, code, None


def pizza_demo():
    # Assumed thermophysical properties of baked dough / crust (typical literature values).
    L, n_cells, k, rho, cp = 0.015, 40, 0.4, 1000.0, 2500.0
    alpha = k / (rho * cp)
    T0, T_inf = 300.0, 737.0
    h_conv = 25.0   # ASSUMPTION: natural/mild-wind convection in ~92 bar CO2 (density ~65 kg/m^3)
    eps = 0.9
    T_mean = 600.0  # linearisation temperature for the radiative coefficient
    h_rad = 4 * eps * 5.670374419e-8 * T_mean**3
    h_eff = h_conv + h_rad
    t_end, n_s = 600.0, 300
    rho_c, th_c, cp_c = 500.0, 0.002, 2000.0

    def run(cells, rtol):
        r = heat_1d(L, cells, alpha, T0, (1e-9, T_inf, k), (h_eff, T_inf, k), t_end, n_s, rtol=rtol)
        c = lumped_capacitance(T0, T_inf, h_conv, 1.0, rho_c * th_c, cp_c, t_end, n_s, emissivity=eps)
        return r, c

    prof, crust = run(n_cells, 1e-8)
    prof_f, crust_f = run(2 * n_cells, 1e-10)
    f_prof = prof_f.state["T"].reshape(n_s, n_cells, 2).mean(axis=2)  # block-average onto coarse grid
    units = {"T_profile": "kelvin", "T_crust": "kelvin"}
    res = SimResult(t=prof.t, state={"T_profile": prof.state["T"], "T_crust": crust.state["T"]}, units=units)
    fine = SimResult(t=prof_f.t, state={"T_profile": f_prof, "T_crust": crust_f.state["T"]}, units=units)

    spec = SimSpec(
        title="Pizza baking on Venus",
        prompt="What happens to a 1.5 cm pizza left on the surface of Venus?",
        domains=[Domain.THERMAL], fidelity=Fidelity.HEURISTIC,
        assumptions=[
            "Venus surface air at 737 K and ~92 bar CO2 (NASA fact sheet)",
            (f"Effective film coefficient h = h_conv + h_rad = {h_conv:g} + {h_rad:.0f} = {h_eff:.0f} W/m^2/K: "
            "h_conv = 25 is an ASSUMED natural-convection value for dense CO2 (no measured value for a "
            "pizza); h_rad = 4*eps*sigma*T^3 with eps = 0.9 at T = 600 K, surroundings at 737 K"),
            ("Pizza modelled as a homogeneous 1.5 cm slab (k = 0.4 W/m/K, rho = 1000 kg/m^3, "
            "cp = 2500 J/kg/K), initially 300 K; no moisture evaporation, chemistry or phase change"),
            "Underside rests on an insulating tray (adiabatic); heat enters through the top face",
            ("Crust: a 2 mm lumped layer (rho = 500 kg/m^3, cp = 2000 J/kg/K) heated by convection at "
            "25 W/m^2/K plus true radiation with eps = 0.9"),
        ],
        parameters=[P("T_inf", T_inf, "kelvin", "Venus surface air temperature", NASA_VENUS),
                    P("h", h_eff, "watt/meter**2/kelvin", "Assumed effective convective+radiative coefficient"),
                    P("k", k, "watt/meter/kelvin", "Pizza thermal conductivity (assumed)"),
                    P("alpha", alpha, "meter**2/second", "Thermal diffusivity k/(rho*cp)"),
                    P("L", L, "meter", "Pizza thickness")],
        state=[S("T_profile", "kelvin", T0, "Temperature through the thickness (x = 0 bottom, x = L top)"),
               S("T_crust", "kelvin", T0, "Lumped top-crust temperature")],
        equations=[Equation(label="heat", expression="dT_crust_dt = alpha*(T_inf - T_crust)/L**2")],
        entities=[Entity(name="pizza", kind="pizza", bound_state=["T_profile", "T_crust"])],
        time=TimeSpan(end=t_end, unit="second", output_samples=n_s),
        bounds=[Bound(variable="T_crust", min=T0 - 1e-6, max=T_inf + 1e-6)],
        citations=[NASA_VENUS, NIST_CODATA],
    )
    research = ResearchOutput(
        summary=("Venus's surface is ~737 K (464 C) at ~92 bar of almost pure CO2, hot enough to melt lead. "
                 "A pizza at 300 K gains heat by convection through dense CO2 and by radiation. With "
                 "alpha ~ 1.6e-7 m^2/s the 1.5 cm slab has a diffusion time L^2/alpha ~ 1400 s, so the top "
                 "crust approaches ambient within a minute or two while the core lags. Property values for "
                 "dough and the film coefficient are assumptions, hence heuristic fidelity."),
        phenomena=["transient conduction", "convection in dense CO2", "thermal radiation"],
        facts=[Fact(name="T_surface_venus", value=737.0, unit="K",
                    description="Mean Venus surface temperature", citation=NASA_VENUS),
               Fact(name="p_surface_venus", value=92.0, unit="bar", description="Venus surface pressure",
                    citation=NASA_VENUS),
               Fact(name="sigma", value=5.670374419e-8, unit="W/m^2/K^4",
                    description="Stefan-Boltzmann constant", citation=NIST_CODATA)],
        open_questions=["The convection coefficient for a pizza on Venus is not measured",
                        "Moisture loss and browning chemistry are not modelled"])
    code = "def simulate(spec, refine=1):\n    ...  # heat_1d + lumped_capacitance\n"
    return "pizza-venus", spec, res, fine, research, code, {"T_profile": "x"}


def evaluate(spec, res, fine, code):
    inp = EvalInput(job_id="demo", prompt=spec.prompt, spec=spec, code=SolverCode(code=code),
                    result_key="", fine_result_key=None)
    results = ev.run_checks(inp, res, fine)
    summary = asyncio.run(ev.critic_hook(inp, res, results))
    return EvalReport(passed=all(c.passed for c in results), checks=results, critic_summary=summary)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for build in (projectile_demo, pendulum_demo, pizza_demo):
        slug, spec, res, fine, research, code, axes = build()
        d = OUT / slug
        d.mkdir(exist_ok=True)
        doc = build_playback(spec, res, field_axes=axes)
        (d / "playback.json").write_bytes(dumps(doc))
        gltf, _ = build_gltf([(e.name, asset_for_kind(e.kind), list(e.bound_state)) for e in spec.entities])
        (d / "scene.gltf").write_bytes(gltf_dumps(gltf))
        report = evaluate(spec, res, fine, code)
        outcome = JobOutcome(job_id=f"demo-{slug}", status="awaiting_review", revisions=0, spec=spec,
                             research=research, eval_report=report,
                             playback_key=f"demo/{slug}/playback.json",
                             scene_key=f"demo/{slug}/scene.gltf")
        (d / "outcome.json").write_text(outcome.model_dump_json(indent=1), encoding="utf-8")
        index.append({"slug": slug, "title": spec.title, "fidelity": spec.fidelity.value})
        bad = [(c.name, c.detail) for c in report.checks if not c.passed]
        print(f"{slug}: eval passed={report.passed} failing={bad}")
    (OUT / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
