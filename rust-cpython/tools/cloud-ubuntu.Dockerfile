FROM ubuntu@sha256:73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8
COPY linux-toolchain.lock.json /tmp/lane-lock.json
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates && \
    python3 -c 'import json; print("\n".join(json.load(open("/tmp/lane-lock.json"))["system_packages"]))' >/tmp/pins && \
    xargs apt-get install -y --no-install-recommends git curl tar xz-utils util-linux procps time dpkg-dev make=4.3-4.1build2 pkgconf=1.8.1-2build1 </tmp/pins && \
    rm -rf /var/lib/apt/lists/* /tmp/pins /tmp/lane-lock.json
