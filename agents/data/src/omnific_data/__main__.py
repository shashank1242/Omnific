from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import prepare_playback

if __name__ == "__main__":
    run_worker(TaskQueue.DATA, activities=[prepare_playback])
