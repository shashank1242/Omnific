from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import build_spec

if __name__ == "__main__":
    run_worker(TaskQueue.MODELER, activities=[build_spec])
