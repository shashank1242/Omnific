# SceneBuild GPU worker: TRELLIS.2 (image -> textured mesh) and UniRig (skeleton + skinning).
# CUDA 12.8 + PyTorch 2.7: the first combination with Blackwell (sm_120, RTX 50-series) kernels; also fine on L4/A100/H100.
# Upstream TRELLIS.2 pins torch 2.6/cu124 and recommends >=24 GB; with low_vram offloading (its default) and
# MESH_GEN_PIPELINE=512 it fits a 12 GB card. UniRig needs ~8 GB.
# Each model keeps its own conda env (their Python/dependency pins differ); our worker runs in a uv venv (Python 3.11)
# and calls them as subprocesses (omnific_mesh_gpu/backends.py). Weights download to $HF_HOME (a volume) on first use.
FROM nvidia/cuda:12.8.1-devel-ubuntu22.04

ARG TRELLIS_REF=75fbf0183001ed9876c8dbb35de6b68552ee08bd
ARG UNIRIG_REF=6793c6640ff01c8fb389f3993434124bb43d2933
ARG TORCH="torch==2.7.1 torchvision==0.22.1"
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128
ARG FLASH_ATTN=https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.7cxx11abiTRUE
# GPU archs to compile the CUDA extensions for: 8.9 = L4, 12.0 = sm_120 (RTX 50-series); compose builds only what the
# local card needs. MAX_JOBS keeps parallel nvcc within Docker Desktop's memory (6 jobs x 5 archs OOM'd a 16 GB VM).
ARG CUDA_ARCHS="8.0;8.6;8.9;9.0;12.0"
ARG MAX_JOBS=2
ENV DEBIAN_FRONTEND=noninteractive CUDA_HOME=/usr/local/cuda TORCH_CUDA_ARCH_LIST=${CUDA_ARCHS} MAX_JOBS=${MAX_JOBS} \
    CONDA_PLUGINS_AUTO_ACCEPT_TOS=yes

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential ninja-build libgl1 libglib2.0-0 libegl1 libjpeg-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/mc.sh \
    && bash /tmp/mc.sh -b -p /opt/conda && rm /tmp/mc.sh
ENV PATH=/opt/conda/bin:$PATH

# TRELLIS.2: our own `trellis2` env (cu128 torch) instead of setup.sh --new-env (cu124), then upstream setup.sh for the
# rest. setup.sh exits unless `nvidia-smi` exists (a build has no GPU) and calls `sudo apt`: shim both for this step.
RUN git clone --recursive https://github.com/microsoft/TRELLIS.2.git /opt/TRELLIS.2 \
    && cd /opt/TRELLIS.2 && git checkout ${TRELLIS_REF} && git submodule update --init --recursive \
    && conda create -y -n trellis2 python=3.10 \
    && /opt/conda/envs/trellis2/bin/pip install ${TORCH} --index-url ${TORCH_INDEX} \
    && /opt/conda/envs/trellis2/bin/pip install "${FLASH_ATTN}-cp310-cp310-linux_x86_64.whl" \
    && mkdir /tmp/shim && printf '#!/bin/sh\nexit 0\n' > /tmp/shim/nvidia-smi && printf '#!/bin/sh\nexec "$@"\n' > /tmp/shim/sudo \
    && chmod +x /tmp/shim/* \
    && PATH="/opt/conda/envs/trellis2/bin:/tmp/shim:$PATH" bash -c ". ./setup.sh --basic --nvdiffrast --nvdiffrec --cumesh --o-voxel --flexgemm" \
    && /opt/conda/envs/trellis2/bin/python -c "import torch; v = torch.__version__; assert v.startswith('2.7.'), v" \
    && rm -rf /tmp/shim /tmp/extensions /root/.cache/pip

# UniRig: Python 3.11 env per its README (flash_attn from the prebuilt wheel: building it takes hours)
RUN git clone https://github.com/VAST-AI-Research/UniRig.git /opt/UniRig \
    && cd /opt/UniRig && git checkout ${UNIRIG_REF} \
    && conda create -y -n unirig python=3.11 \
    && /opt/conda/envs/unirig/bin/pip install ${TORCH} --index-url ${TORCH_INDEX} \
    && /opt/conda/envs/unirig/bin/pip install "${FLASH_ATTN}-cp311-cp311-linux_x86_64.whl" \
    && /opt/conda/envs/unirig/bin/pip install -r requirements.txt \
    && /opt/conda/envs/unirig/bin/pip install spconv-cu126 numpy==1.26.4 \
    && /opt/conda/envs/unirig/bin/pip install torch_scatter torch_cluster -f https://data.pyg.org/whl/torch-2.7.0+cu128.html \
    && /opt/conda/envs/unirig/bin/python -c "import torch; v = torch.__version__; assert v.startswith('2.7.'), v" \
    && rm -rf /root/.cache/pip

# UniRig's bpy (Blender as a module) links X11 client libraries even headless. A late layer, so adding it doesn't
# rebuild the CUDA extensions above.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxrender1 libxi6 libxkbcommon0 libsm6 libxxf86vm1 libxfixes3 libxext6 libx11-6 \
    && rm -rf /var/lib/apt/lists/*

# Our worker
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
# Python outside /root so the non-root worker (uid 10001) can run the venv interpreter
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
WORKDIR /app
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked uv python install 3.11 && UV_LINK_MODE=copy uv sync --frozen --no-dev --package omnific-mesh-gpu --python 3.11 \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
# UniRig's launch scripts call `python`: backends.py puts its env first for those subprocesses only
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TRELLIS_PYTHON=/opt/conda/envs/trellis2/bin/python TRELLIS_DIR=/opt/TRELLIS.2 \
    UNIRIG_DIR=/opt/UniRig UNIRIG_PYTHON=/opt/conda/envs/unirig/bin/python \
    HF_HOME=/models HOME=/tmp
USER 10001:10001
CMD ["python", "-m", "omnific_mesh_gpu"]
