from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .chat import SimulationChat
from .choices import AssetChoicesWorkflow
from .dress import BlendExport, SceneDress
from .ingest import DocumentIngest
from .scene import SceneBuild
from .simulation import SimulationJob
from .variant import SimulationVariant

if __name__ == "__main__":
    run_worker(TaskQueue.WORKFLOW, workflows=[SimulationJob, DocumentIngest, SceneBuild, SimulationVariant, SceneDress,
                                                BlendExport, AssetChoicesWorkflow, SimulationChat])
