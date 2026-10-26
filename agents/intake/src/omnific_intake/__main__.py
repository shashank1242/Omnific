from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import interpret_inputs

if __name__ == "__main__":
    run_worker(TaskQueue.INTAKE, activities=[interpret_inputs])
