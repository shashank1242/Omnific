"""SimulationChat: one question, answered by the researcher worker's chat activity."""

import uuid

from omnific_common.contracts import CHAT_ACTIVITY, ChatInput, ChatMessage, ChatReply, TaskQueue
from omnific_common.temporal import workflow_runner
from omnific_sim.spec import Domain, Entity, Fidelity, SimSpec, TimeSpan
from omnific_workflows.chat import SimulationChat
from temporalio import activity
from temporalio.worker import Worker
from test_simulation import env  # noqa: F401

SPEC = SimSpec(title="t", prompt="p", domains=[Domain.MECHANICS], fidelity=Fidelity.HEURISTIC, assumptions=[],
               parameters=[], state=[], equations=[], time=TimeSpan(end=1.0), entities=[Entity(name="ball", kind="ball")])


async def test_a_question_is_answered_on_the_researcher_queue(env):  # noqa: F811
    seen = []

    @activity.defn(name=CHAT_ACTIVITY)
    async def answer(inp: ChatInput) -> ChatReply:
        seen.append(inp)
        return ChatReply(answer=f"About {inp.spec.title}: {inp.messages[-1].content}", grounded_in=["simulation"],
                         follow_ups=[])

    async with Worker(env.client, task_queue=TaskQueue.WORKFLOW.value, workflows=[SimulationChat],
                      workflow_runner=workflow_runner()), \
            Worker(env.client, task_queue=TaskQueue.RESEARCHER.value, activities=[answer]):
        out = await env.client.execute_workflow(
            "SimulationChat", ChatInput(job_id="j", prompt="p", spec=SPEC, messages=[ChatMessage(role="user", content="why?")]),
            id=f"chat-{uuid.uuid4().hex}", task_queue=TaskQueue.WORKFLOW.value, result_type=ChatReply)
    assert out.answer == "About t: why?" and seen[0].job_id == "j"
