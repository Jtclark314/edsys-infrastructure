# syntax=docker/dockerfile:1.7

FROM docker.io/library/golang@sha256:111d79159b2326f7e80c4a4706e1ba166acb0e2611df853955f3621828cd49e8 AS builder

ARG ZOEKT_REVISION=2cb19912a4073e5a9895658b7cb135ee4b35733b
ARG ZOEKT_SOURCE_SHA256=7728e3498828c36cc4fea935d95c219ed9eddd082750d218ace8d58d15c85d2a
ARG CTAGS_VERSION=6.1.0
ARG CTAGS_SOURCE_SHA256=1eb6d46d4c4cace62d230e7700033b8db9ad3d654f2d4564e87f517d4b652a53

ENV CGO_ENABLED=0 \
    GOFLAGS="-buildvcs=false -mod=readonly"

ADD --checksum=sha256:7728e3498828c36cc4fea935d95c219ed9eddd082750d218ace8d58d15c85d2a \
    https://github.com/sourcegraph/zoekt/archive/2cb19912a4073e5a9895658b7cb135ee4b35733b.tar.gz \
    /tmp/zoekt.tar.gz
ADD --checksum=sha256:1eb6d46d4c4cace62d230e7700033b8db9ad3d654f2d4564e87f517d4b652a53 \
    https://codeload.github.com/universal-ctags/ctags/tar.gz/refs/tags/v6.1.0 \
    /tmp/ctags.tar.gz

RUN mkdir -p /src /out \
    && tar -xzf /tmp/zoekt.tar.gz --strip-components=1 -C /src \
    && test "$(sha256sum /tmp/zoekt.tar.gz | cut -d' ' -f1)" = "${ZOEKT_SOURCE_SHA256}" \
    && test "$(sha256sum /tmp/ctags.tar.gz | cut -d' ' -f1)" = "${CTAGS_SOURCE_SHA256}"

WORKDIR /src

COPY healthcheck.go /tmp/healthcheck.go

RUN go build -trimpath -tags netgo,osusergo -ldflags="-s -w -buildid=" \
       -o /out/zoekt ./cmd/zoekt \
    && go build -trimpath -tags netgo,osusergo -ldflags="-s -w -buildid=" \
       -o /out/zoekt-git-index ./cmd/zoekt-git-index \
    && go build -trimpath -tags netgo,osusergo -ldflags="-s -w -buildid=" \
       -o /out/zoekt-webserver ./cmd/zoekt-webserver \
    && go build -trimpath -tags netgo,osusergo -ldflags="-s -w -buildid=" \
       -o /out/zoekt-healthcheck /tmp/healthcheck.go \
    && /out/zoekt-webserver -version

RUN apk add --no-cache \
      autoconf \
      automake \
      build-base \
      jansson-dev \
      libseccomp-dev \
      pkgconf \
    && mkdir -p /ctags-src /ctags-root \
    && tar -xzf /tmp/ctags.tar.gz --strip-components=1 -C /ctags-src \
    && cd /ctags-src \
    && ./autogen.sh \
    && ./configure \
       --program-prefix=universal- \
       --enable-json \
       --enable-seccomp \
       --disable-readcmd \
    && make -j"$(getconf _NPROCESSORS_ONLN)" \
    && make install DESTDIR=/ctags-root \
    && /ctags-root/usr/local/bin/universal-ctags --version | grep -F "Universal Ctags ${CTAGS_VERSION}"

FROM scratch

ARG ZOEKT_REVISION=2cb19912a4073e5a9895658b7cb135ee4b35733b

LABEL org.opencontainers.image.title="EdSys minimal Zoekt runtime" \
      org.opencontainers.image.description="Pinned, static Zoekt search and index binaries for the private 9950x code-intelligence stack" \
      org.opencontainers.image.source="https://github.com/sourcegraph/zoekt" \
      org.opencontainers.image.revision="${ZOEKT_REVISION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      com.edsys.runtime="zoekt-minimal" \
      com.edsys.go.version="1.26.5" \
      com.edsys.ctags.version="6.1.0"

ENV PATH=/usr/local/bin

COPY --from=builder /out/zoekt /usr/local/bin/zoekt
COPY --from=builder /out/zoekt-git-index /usr/local/bin/zoekt-git-index
COPY --from=builder /out/zoekt-webserver /usr/local/bin/zoekt-webserver
COPY --from=builder /out/zoekt-healthcheck /usr/local/bin/zoekt-healthcheck
COPY --from=builder /ctags-root/usr/local/bin/universal-ctags /usr/local/bin/universal-ctags
COPY --from=builder /lib/ld-musl-x86_64.so.1 /lib/ld-musl-x86_64.so.1
COPY --from=builder /usr/lib/libjansson.so.4* /usr/lib/
COPY --from=builder /usr/lib/libseccomp.so.2* /usr/lib/

USER 100:101
