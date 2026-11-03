# Host build tools only. Target headers/libraries come exclusively from /sdk and /deps.
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates python3 cmake ninja-build make clang lld binutils pkg-config \
    perl ruby bison flex gperf patch file git zstd \
    && dpkg-query -W > /usr/share/libs-builder-packages.txt \
    && rm -rf /var/lib/apt/lists/*
ARG LIBS_RUNNER_SHA256
LABEL org.mochios.libs.runner-sha256=$LIBS_RUNNER_SHA256
COPY tools/container_build.py /opt/libs/container_build.py
COPY tooling/probe /opt/libs/probe
ENV HOME=/tmp CARGO_NET_OFFLINE=true
ENTRYPOINT ["python3", "/opt/libs/container_build.py"]
