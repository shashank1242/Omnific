from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import write_solver

if __name__ == "__main__":
    run_worker(TaskQueue.SOLVER_CODER, activities=[write_solver])
