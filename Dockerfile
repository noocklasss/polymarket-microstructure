# The recorder and research package in a container.
#   docker build -t pmlab .
#   docker run --rm pmlab families
#   docker run -d --name btc_5m --restart unless-stopped -v "$PWD/data:/app/data" pmlab record btc_5m
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . \
    && useradd --create-home --uid 1000 pmlab \
    && mkdir -p /app/data \
    && chown pmlab:pmlab /app/data

# Recordings are written to /app/data: mount a folder of the host there to keep them.
USER pmlab
VOLUME ["/app/data"]
ENTRYPOINT ["pmlab"]
CMD ["families"]
