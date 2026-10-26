"""Workflow + activity for test_sandbox_units (a real module: the sandbox re-imports workflow modules by name)."""

from datetime import timedelta

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from omnific_sim.spec import Domain, Fidelity, Parameter, SimSpec, TimeSpan


@activity.defn(name="model_stub")
async def model_stub() -> SimSpec:
    # model_construct: no validation here, so -- as in the real workflows worker, which never parses units outside
    # the sandbox -- the FIRST pint parse happens while the workflow decodes this result (SimSpec validates units)
    p = Parameter.model_construct(name="rho", value=1000.0, unit="kilogram/meter**3", description="", source=None)
    return SimSpec.model_construct(
        title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
        parameters=[p], state=[], equations=[], time=TimeSpan(end=1.0))


@workflow.defn(name="UnitsWf")
class UnitsWf:
    @workflow.run
    async def run(self) -> str:
        spec = await workflow.execute_activity("model_stub", result_type=SimSpec,
                                               start_to_close_timeout=timedelta(seconds=30))
        return spec.parameters[0].unit
