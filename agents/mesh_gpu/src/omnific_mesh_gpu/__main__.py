from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import GPU_ACTIVITIES

if __name__ == "__main__":
    # one model on the GPU at a time: TRELLIS.2 alone needs ~24 GB
    run_worker(TaskQueue.MESH_GPU, activities=GPU_ACTIVITIES, max_concurrent_activities=1)
