# Blender runner image for scene dressing (LLM-written Blender scripts) and .blend export. Contains ONLY Python 3.11,
# the `bpy` module and the harness files: no omnific packages, no credentials. Runs like the solver and CAD runners:
# no network, read-only root, non-root, under gVisor on Kubernetes (OMNIFIC_EMIT_RESULT=stdout set by the Job spec).
# Stills for the vision critic render with Cycles on the CPU, so no GPU or OpenGL driver is needed.
# Build (context = repo root): docker build -f sandbox/blender-runner.Dockerfile -t omnific-blender-runner:latest .
# amd64 only: `bpy` publishes no Linux ARM64 wheel (on an ARM machine Docker builds and runs it emulated).
FROM --platform=linux/amd64 python:3.11-slim
# bpy links against X11/GL client libraries even when nothing is displayed (libusb: see the GPU image notes)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libx11-6 libxrender1 libxxf86vm1 libxfixes3 libxi6 libxkbcommon0 libxext6 libsm6 libice6 \
        libgl1 libegl1 libglib2.0-0 libgomp1 libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/pip pip install bpy==5.0.1
COPY sandbox/src/omnific_sandbox/harness.py sandbox/src/omnific_sandbox/blender_harness.py \
     sandbox/src/omnific_sandbox/playback_math.py sandbox/src/omnific_sandbox/gltf_merge.py \
     sandbox/src/omnific_sandbox/blender_safety.py sandbox/src/omnific_sandbox/blender_story.py /opt/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 65534:65534
ENTRYPOINT ["python", "/opt/blender_harness.py"]
