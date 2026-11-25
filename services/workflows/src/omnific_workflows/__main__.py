from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .ingest import DocumentIngest
from .scene import SceneBuild
from .simulation import SimulationJob
from .variant import SimulationVariant

if __name__ == "__main__":
    run_worker(TaskQueue.WORKFLOW, workflows=[SimulationJob, DocumentIngest, SceneBuild, SimulationVariant])
