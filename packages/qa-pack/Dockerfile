# syntax=docker/dockerfile:1
FROM python:3.12-slim
ARG VERSION
RUN pip install --no-cache-dir "devin-qa-pack==${VERSION}"
LABEL org.opencontainers.image.source="https://github.com/Icaro0310/devin-assure" \
      org.opencontainers.image.description="devin-qa-pack CLI: evidence audits for agent sessions in CI"
ENTRYPOINT ["devin-qa-pack"]
CMD ["--help"]
