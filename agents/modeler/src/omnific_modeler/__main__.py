from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import build_spec
from .edit import classify_edit

if __name__ == "__main__":
    run_worker(TaskQueue.MODELER, activities=[build_spec, classify_edit])
