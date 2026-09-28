# syntax=docker/dockerfile:1
# Pre-peer-checker — DGX Spark (aarch64) / NVIDIA GPU
#
# Targets:
#   runtime  — 照合実行・pytest（軽量、torch 任意）
#   train    — DINOv2 / LoRA 等の学習・GPU 推論（PyTorch + imaging）

ARG CUDA_IMAGE=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
ARG CUDA_DEVEL_IMAGE=nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04

# ---------------------------------------------------------------------------
# runtime: 照合パイプライン実行
# ---------------------------------------------------------------------------
FROM ${CUDA_IMAGE} AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PRE_PEER_CHECKER_HOME=/app \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        git \
        build-essential \
        libgl1 \
        libglib2.0-0 \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python3 -m venv /opt/venv
COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY fixtures ./fixtures

RUN pip install -U pip setuptools wheel \
    && pip install -e ".[dev,r-ast]" \
    && pip install tree-sitter \
    && (pip install "git+https://github.com/r-lib/tree-sitter-r" || true)

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /data/input /data/output /data/models /data/reference

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["verify"]

# ---------------------------------------------------------------------------
# train: GPU 学習・DINOv2 / 将来の LoRA
# ---------------------------------------------------------------------------
FROM ${CUDA_DEVEL_IMAGE} AS train

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PRE_PEER_CHECKER_HOME=/app \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        git \
        build-essential \
        libgl1 \
        libglib2.0-0 \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python3 -m venv /opt/venv
COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY fixtures ./fixtures

# PyTorch: ホスト CUDA と近い wheel を取得（aarch64 対応状況に依存）
# 失敗時は CPU wheel にフォールバックし、コンテナ起動は可能にする
RUN pip install -U pip setuptools wheel \
    && (pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128 \
        || pip install torch torchvision) \
    && pip install -e ".[dev,imaging,r-ast]" \
    && pip install tree-sitter \
    && (pip install "git+https://github.com/r-lib/tree-sitter-r" || true)

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/train_stub.py /app/docker/train_stub.py
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /data/input /data/output /data/models /data/reference

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["train"]
