from omnific_common.contracts import TaskQueue
from omnific_common.temporal import run_worker

from .activities import ingest_document

if __name__ == "__main__":
    run_worker(TaskQueue.LIBRARIAN, activities=[ingest_document])
