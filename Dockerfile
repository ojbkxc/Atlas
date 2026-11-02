# Linux 验收镜像：不含凭据，只验证跨平台执行能力。
# 基础镜像可选：默认从官方 python 拉取；离线/受限网络下用本地已有镜像覆盖：
#   docker build --build-arg BASE_IMAGE=debian:bookworm-slim -t atlas:tmp .
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    ATLAS_BROWSER_EXECUTABLE=/usr/bin/chromium \
    ATLAS_BROWSER_CHANNEL=chromium

WORKDIR /app

# 统一走 apt（Debian/Ubuntu 系均可），装 Python 与系统 chromium。
# 用系统浏览器可避开 Playwright 浏览器 CDN 不可达的问题。
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv ca-certificates chromium \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
COPY examples ./examples

RUN python3 -m pip install --no-cache-dir --break-system-packages -e . \
 && python3 -m pip install --no-cache-dir --break-system-packages pytest

# 离线门禁：不联网业务系统、不需要凭据
CMD ["sh", "-c", "python3 -m pytest -q 2>&1 | tail -4 && echo '--- compile ---' && python3 -m compileall -q src scripts examples && echo COMPILE_OK && python3 examples/smoke_browser.py && echo LINUX_ACCEPTANCE_PASSED"]