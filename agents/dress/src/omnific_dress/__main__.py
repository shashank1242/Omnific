from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import DRESS_ACTIVITIES

if __name__ == "__main__":
    run_worker(TaskQueue.DRESS, activities=DRESS_ACTIVITIES)
