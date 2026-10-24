# Runner image for generated solver code. Contains ONLY omnific-sim (+deps) and the harness.
# Build (context = repo root): docker build -f sandbox/runner.Dockerfile -t omnific-runner:latest .
# On Kubernetes this image runs as a Job under gVisor (OMNIFIC_EMIT_RESULT=stdout is set by the Job spec).
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --package omnific-sim \
    && rm -rf /app/agents /app/services /app/sandbox /app/apps /app/exporters /app/deploy /app/docs
COPY sandbox/src/omnific_sandbox/harness.py /opt/harness.py
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python", "/opt/harness.py"]
