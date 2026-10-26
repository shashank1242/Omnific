# CAD runner image for generated build123d code (mesh router, cad route). Contains ONLY build123d/OCP, numpy, the CAD
# harness and its joint helper: no omnific packages, no credentials. Runs like the solver runner: no network,
# read-only root, non-root, under gVisor on Kubernetes (OMNIFIC_EMIT_RESULT=stdout set by the Job spec).
# Build (context = repo root): docker build -f sandbox/cad-runner.Dockerfile -t omnific-cad-runner:latest .
FROM python:3.12-slim
# OCP's wheel links against libGL / X11 libraries even when nothing is displayed
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxrender1 libxext6 \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/pip pip install build123d==0.13.0 numpy
COPY sandbox/src/omnific_sandbox/harness.py sandbox/src/omnific_sandbox/cad_harness.py \
     sandbox/src/omnific_sandbox/omnific_cad.py /opt/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 65534:65534
ENTRYPOINT ["python", "/opt/cad_harness.py"]
