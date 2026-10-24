from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import research

if __name__ == "__main__":
    run_worker(TaskQueue.RESEARCHER, activities=[research])
