# syntax=docker/dockerfile:1
FROM python:3.12-slim
ARG VERSION
RUN pip install --no-cache-dir "devin-evals==${VERSION}"
LABEL org.opencontainers.image.source="https://github.com/Icaro0310/devin-assure" \
      org.opencontainers.image.description="devin-evals CLI: corpus regression verification in CI"
ENTRYPOINT ["devin-evals"]
CMD ["--help"]
