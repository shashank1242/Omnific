from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .simulation import SimulationJob

if __name__ == "__main__":
    run_worker(TaskQueue.WORKFLOW, workflows=[SimulationJob])
