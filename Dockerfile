FROM debian:stable-slim

ENV DEBIAN_FRONTEND=noninteractive

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-dev \
    git git-lfs curl ca-certificates \
    nodejs npm \
    xvfb \
    build-essential \
    && git lfs install \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Python deps
ENV VIRTUAL_ENV=/opt/atomix-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
RUN python3 -m venv "${VIRTUAL_ENV}" && \
    python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install -r requirements.txt

# Playwright browsers for WebArena
RUN npx --yes playwright install chromium

# Data root
VOLUME ["/data", "/app/results", "/app/logs"]
ENV DATA_ROOT=/data \
    WEBARENA_DATA_DIR=/data/webarena \
    SWE_BENCH_DATA_DIR=/data/swebench

# Default command: run sample experiments (user can override)
CMD ["/bin/bash"]
