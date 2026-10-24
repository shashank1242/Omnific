# Service conventions

Every agent and service follows the same shape, so the workflow, Dockerfiles, Helm charts and tests all stay uniform.

## Layout
```
agents/<name>/
  pyproject.toml            # workspace member, package omnific_<name>
  src/omnific_<name>/
    activities.py           # the one @activity.defn named ACTIVITY[TaskQueue.<NAME>]
    prompts.py              # LLM agents only: instructions strings
    __main__.py             # run_worker(TaskQueue.<NAME>, activities=[...])
  tests/                    # pytest, no network, no real OpenAI calls
  Dockerfile                # build context = repo root
```

## Rules
- Contracts live only in `omnific_common.contracts` and `omnific_sim.spec`. Don't define new cross-service models
  inside an agent. If one is needed, flag it so it can be added centrally.
- Activity: `@activity.defn(name=ACTIVITY[TaskQueue.X])`, async, typed with contract models in and out.
- LLM access goes only through `omnific_common.llm.LLM().structured(...)`. In tests, inject a fake by
  constructing activities with a stub `LLM` (use a module-level `llm = LLM()` that tests monkeypatch).
- Artifacts: `omnific_common.storage.store_from_env()` and `job_key(job_id, name)`. Tests use `LocalStore(tmp_path)`.
- Validation failures that a retry can't fix: raise `temporalio.exceptions.ApplicationError(..., non_retryable=True)`.
- Environment: run everything with `uv run --no-sync ...` from the repo root. Don't `uv add` or `pip install`.
  Ask for dependencies to be added centrally.
- Tests: `uv run --no-sync pytest -q agents/<name>`; lint: `uv run --no-sync ruff check agents/<name>`.

## Dockerfile template
```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --package omnific-<name>
CMD ["uv", "run", "--no-sync", "--package", "omnific-<name>", "python", "-m", "omnific_<name>"]
```

## Environment variables
`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `OPENAI_API_KEY`, `MODEL_REASONING`, `MODEL_GENERAL`, `MODEL_FAST`,
`OPENAI_WEB_SEARCH_TOOL`, `ARTIFACT_STORE` (`local`|`s3`|`gcs`), `ARTIFACT_DIR`, `GCS_BUCKET`, `S3_BUCKET`, `S3_ENDPOINT_URL`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `SANDBOX_BACKEND` (`docker`|`subprocess`|`k8s`), `SANDBOX_IMAGE`, `SANDBOX_NAMESPACE`, `SANDBOX_RUNTIME_CLASS`, `SANDBOX_WORK_DIR` (must be the same path on the
Docker host and in the sandbox worker), `EVAL_CRITIC` (`off` disables the LLM critic), `EVAL_CONVERGENCE_TOL`.
