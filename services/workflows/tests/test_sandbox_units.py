"""Regression: decoding unit-validated contracts inside the workflow sandbox froze jobs.

pint lazily imports flexcache, whose module body calls platform.system(); the Temporal sandbox forbids that, so a
SimulationJob receiving research facts with e.g. unit 'meter**2' failed every workflow task and never progressed.
The fix: every workflow worker uses omnific_common.temporal.workflow_runner() (pint & co. passed through).
"""

import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).parent


def run(*args) -> subprocess.CompletedProcess:
    # A fresh interpreter, like a freshly started worker container.
    code = textwrap.dedent('''
        import asyncio, sys, uuid
        from datetime import timedelta
        sys.path.insert(0, sys.argv[1])
        from omnific_common.temporal import workflow_runner
        from temporalio.contrib.pydantic import pydantic_data_converter
        from temporalio.testing import WorkflowEnvironment
        from temporalio.worker import Worker
        from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
        from units_wf import UnitsWf, model_stub
        runner = SandboxedWorkflowRunner() if "--default-runner" in sys.argv else workflow_runner()

        async def main():
            env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
            async with env:
                async with Worker(env.client, task_queue="q", workflows=[UnitsWf], activities=[model_stub],
                                  workflow_runner=runner):
                    try:
                        r = await env.client.execute_workflow("UnitsWf", id=uuid.uuid4().hex, task_queue="q",
                                                              execution_timeout=timedelta(seconds=20))
                        print("RESULT", r)
                    except Exception as e:
                        print("FAILED", type(e).__name__)

        asyncio.run(main())
    ''')
    return subprocess.run([sys.executable, "-c", code, str(HERE), *args], capture_output=True, text=True, check=False,
                          timeout=240)


def test_unit_validation_inside_workflow_sandbox():
    p = run()
    assert "RESULT kilogram/meter**3" in p.stdout, p.stdout[-2000:] + p.stderr[-3000:]
