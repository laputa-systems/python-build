# Generic Linux amd64 benchmark environment. Interpreters, locked dependency
# prefixes, and wheelhouses arrive as read-only runtime mounts; no baseline or
# candidate is part of the image.
FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf

RUN apk add --no-cache \
      bash=5.3.9-r1 \
      ca-certificates=20261020-r0 \
      coreutils=9.11-r0 \
      libstdc++=15.2.0-r5 \
      perf=7.1.5-r0 \
      procps-ng=4.0.6-r0 \
      python3=3.14.10-r1 \
      tar=1.35-r5 \
    && [ "$(uname -m)" = "x86_64" ] \
    || (echo "benchmark image is Linux amd64 only" >&2; exit 1)

ENV PYTHONHASHSEED=0 \
    PYTHONNOUSERSITE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /workspace

# The controller bind-mounts benchmark code, inputs, and artifacts at runtime.
# docker run replaces CMD with the command supplied by the caller.
CMD ["python3", "/workspace/benchmarks/bench.py", "--help"]
