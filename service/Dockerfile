# The epm service: epm plus service/, served on $PORT. Build from the repository root:
#   docker build -f service/Dockerfile -t epm-service .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    EPM_DATA=/data

WORKDIR /app

# Dependencies first, so code changes don't reinstall them. epm is installed (not just copied)
# because the custom-function worker starts with `python -I`, which ignores the working
# directory.
COPY pyproject.toml README.md ./
COPY epm ./epm
COPY service/requirements.txt service/requirements.txt
RUN pip install ".[plot]" -r service/requirements.txt

COPY service ./service
COPY examples ./examples

# /data holds the records database (epm.db). Mount a volume or a host directory there and
# back it up: the model files are in R2, but which file is which project is recorded here.
RUN useradd --create-home --uid 10001 epm \
    && mkdir -p /data \
    && chown epm:epm /data
USER epm
VOLUME /data

EXPOSE 10000
CMD ["sh", "-c", "exec python -m service.service --host 0.0.0.0 --port ${PORT:-10000}"]
