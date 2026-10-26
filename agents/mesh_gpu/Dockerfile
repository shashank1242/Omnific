# SceneBuild GPU worker: TRELLIS.2 (image -> textured mesh) and UniRig (skeleton + skinning).
# Upstream pins: TRELLIS.2 needs Linux, CUDA 12.4 and a >=24 GB GPU (verified upstream on A100/H100); UniRig ~8 GB.
# Each model keeps its own conda env (their torch/CUDA pins differ); our worker runs in a uv venv (Python 3.11)
# and calls them as subprocesses (omnific_mesh_gpu/backends.py). Weights download to $HF_HOME (a volume) on first use.
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

ARG TRELLIS_REF=75fbf0183001ed9876c8dbb35de6b68552ee08bd
ARG UNIRIG_REF=6793c6640ff01c8fb389f3993434124bb43d2933
ENV DEBIAN_FRONTEND=noninteractive CUDA_HOME=/usr/local/cuda TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0"

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential ninja-build libgl1 libglib2.0-0 libegl1 \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/mc.sh \
    && bash /tmp/mc.sh -b -p /opt/conda && rm /tmp/mc.sh
ENV PATH=/opt/conda/bin:$PATH

# TRELLIS.2: upstream setup script builds its own `trellis2` env (PyTorch 2.6 / CUDA 12.4)
RUN git clone --recursive https://github.com/microsoft/TRELLIS.2.git /opt/TRELLIS.2 \
    && cd /opt/TRELLIS.2 && git checkout ${TRELLIS_REF} && git submodule update --init --recursive \
    && bash -c ". ./setup.sh --new-env --basic --flash-attn --nvdiffrast --nvdiffrec --cumesh --o-voxel --flexgemm"

# UniRig: Python 3.11 env per its README
RUN git clone https://github.com/VAST-AI-Research/UniRig.git /opt/UniRig \
    && cd /opt/UniRig && git checkout ${UNIRIG_REF} \
    && conda create -y -n unirig python=3.11 \
    && /opt/conda/envs/unirig/bin/pip install torch torchvision \
    && /opt/conda/envs/unirig/bin/pip install -r requirements.txt \
    && /opt/conda/envs/unirig/bin/pip install spconv-cu124 numpy==1.26.4 \
    && /opt/conda/envs/unirig/bin/pip install torch_scatter torch_cluster \
         -f https://data.pyg.org/whl/torch-$(/opt/conda/envs/unirig/bin/python -c 'import torch;print(torch.__version__.split("+")[0])')+cu124.html

# Our worker
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
# Python outside /root so the non-root worker (uid 10001) can run the venv interpreter
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
WORKDIR /app
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked uv python install 3.11 && UV_LINK_MODE=copy uv sync --frozen --no-dev --package omnific-mesh-gpu --python 3.11 \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
# UniRig's launch scripts call `python`: put its env first for those subprocesses only
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TRELLIS_PYTHON=/opt/conda/envs/trellis2/bin/python TRELLIS_DIR=/opt/TRELLIS.2 \
    UNIRIG_DIR=/opt/UniRig UNIRIG_PYTHON=/opt/conda/envs/unirig/bin/python \
    HF_HOME=/models HOME=/tmp
USER 10001:10001
CMD ["python", "-m", "omnific_mesh_gpu"]
