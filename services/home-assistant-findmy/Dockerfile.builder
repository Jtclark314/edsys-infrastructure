FROM rust:1-bookworm@sha256:e12566d5abc17a0da741628782cdb504b68b8961f5bfcf677936bf3e8b32de29
RUN apt-get update && apt-get install -y --no-install-recommends protobuf-compiler libssl-dev pkg-config cmake perl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /build
ENV CARGO_HOME=/build/cargo CARGO_TARGET_DIR=/build/target CARGO_BUILD_JOBS=4
USER 1000:1000
