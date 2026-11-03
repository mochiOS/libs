# Host build tools only. Target headers/libraries come exclusively from /sdk and /deps.
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates python3 cmake ninja-build make clang lld binutils pkg-config \
    perl ruby bison flex gperf patch file git zstd autoconf automake libtool libltdl-dev texinfo python3-venv \
    && dpkg-query -W > /usr/share/libs-builder-packages.txt \
    && rm -rf /var/lib/apt/lists/*
COPY tooling/host-requirements.txt /opt/host-requirements.txt
RUN python3 -m venv /opt/host-tools && /opt/host-tools/bin/pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r /opt/host-requirements.txt
ENV PATH=/opt/host-tools/bin:$PATH
ARG LIBS_RUNNER_SHA256
LABEL org.mochios.libs.runner-sha256=$LIBS_RUNNER_SHA256
COPY tools/container_build.py tools/build_backends.py /opt/libs/
COPY tooling/probe /opt/libs/probe
ENV HOME=/tmp CARGO_NET_OFFLINE=true
ENTRYPOINT ["python3", "/opt/libs/container_build.py"]
