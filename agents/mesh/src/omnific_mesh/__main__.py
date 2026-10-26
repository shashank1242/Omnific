from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import SCENE_ACTIVITIES, build_scene

if __name__ == "__main__":
    run_worker(TaskQueue.MESH, activities=[build_scene, *SCENE_ACTIVITIES])
