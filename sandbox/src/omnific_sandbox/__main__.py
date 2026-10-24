from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import run_solver

if __name__ == "__main__":
    run_worker(TaskQueue.SANDBOX, activities=[run_solver])
