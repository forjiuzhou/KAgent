FROM python:3.12-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash noteweaver

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir -e ".[all]"

RUN mkdir -p /data/vault && chown -R noteweaver:noteweaver /data

USER noteweaver

ENV NW_VAULT=/data/vault

EXPOSE 8384

ENTRYPOINT ["nw"]
CMD ["gateway"]
