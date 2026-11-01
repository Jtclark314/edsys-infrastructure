# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Core backend and core editor only. Do not substitute the standard proprietary image.
FROM node:24.22.2-bookworm-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c AS editor
ADD --checksum=sha256:3f2d3f2cd960b0f12e2557630848616a396c6a3c4798f5d420959fee0180f454 https://github.com/Stirling-Tools/Stirling-PDF/archive/refs/tags/v3.2.2.tar.gz /tmp/stirling-source.tar.gz
RUN mkdir /source && tar -xzf /tmp/stirling-source.tar.gz -C /source --strip-components=1
WORKDIR /source/frontend
RUN npm ci --no-audit --no-fund
ENV STIRLING_FLAVOR=core VITE_INCLUDE_PORTAL=false VITE_API_BASE_URL=/ UV_THREADPOOL_SIZE=4
# Clear optional vendor integrations at build time as well as at runtime.
ENV VITE_GOOGLE_DRIVE_CLIENT_ID="" VITE_GOOGLE_DRIVE_API_KEY="" VITE_GOOGLE_DRIVE_APP_ID="" \
    VITE_SUPABASE_URL="" VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY="" VITE_STRIPE_PUBLISHABLE_KEY="" \
    VITE_PUBLIC_POSTHOG_KEY="" VITE_PUBLIC_POSTHOG_HOST="" VITE_CALENDLY_URL=""
RUN node scripts/generate-og-metadata.mjs && npx vite build --mode core --sourcemap hidden
COPY verify-editor-build.mjs /tmp/verify-editor-build.mjs
RUN node /tmp/verify-editor-build.mjs /source/frontend/dist

FROM node:24.22.2-bookworm-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c AS ocr-data
ADD --checksum=sha256:7584b6bdba03bb920962bd57608a0acabc1b7b6cbd2144b412ff830f6a6db4f1 https://codeload.github.com/tesseract-ocr/tessdata_best/tar.gz/e12c65a915945e4c28e237a9b52bc4a8f39a0cec /tmp/tessdata.tar.gz
RUN mkdir /tessdata && tar -xzf /tmp/tessdata.tar.gz -C /tessdata --strip-components=1 \
    && find /tessdata -type f ! -name '*.traineddata' ! -name LICENSE -delete

FROM eclipse-temurin:25-jdk-noble@sha256:95e350ad1eb738f943e141640f7e3d797e483359a3b33c3864255bfd1ad71f8a AS compiler
FROM stirlingtools/stirling-pdf-base:1.1.0@sha256:3bcc8e10d5319b7c0550cf41b96f6980e1552c974dc37aba462b74fa17c4b5e4 AS backend
WORKDIR /patch
COPY --from=compiler /opt/java/openjdk /opt/java/openjdk
ADD --checksum=sha256:370a4218624fa4b3cf5460c886f03de01bfcfd2d4893009202a81e53b5f3344d https://github.com/Stirling-Tools/Stirling-PDF/releases/download/v3.2.2/Stirling-PDF-server.jar /patch/original.jar
ADD --checksum=sha256:01f7b1a015e33e2b62d5f5f37053306357ab1415fd181fcba7794f5d198c1126 https://repo.maven.apache.org/maven2/org/projectlombok/lombok/1.18.46/lombok-1.18.46.jar /patch/lombok.jar
COPY --from=editor /source/app/core/src/main/java/stirling/software/SPDF/controller/api/converters/ConvertWebsiteToPDF.java /patch/ConvertWebsiteToPDF.java
COPY patch-url-context.py /patch/patch-url-context.py
RUN python3 /patch/patch-url-context.py

FROM stirlingtools/stirling-pdf-base:1.1.0@sha256:3bcc8e10d5319b7c0550cf41b96f6980e1552c974dc37aba462b74fa17c4b5e4
WORKDIR /app
ADD --checksum=sha256:370a4218624fa4b3cf5460c886f03de01bfcfd2d4893009202a81e53b5f3344d https://github.com/Stirling-Tools/Stirling-PDF/releases/download/v3.2.2/Stirling-PDF-server.jar /app/app.jar
COPY --from=editor /source/frontend/dist /opt/stirling/static
COPY --from=editor /source/LICENSE /opt/stirling/LICENSE
COPY --from=editor /source/scripts /scripts
COPY --from=editor /source/app/core/src/main/resources/static/fonts /usr/share/fonts/truetype/stirling
COPY --from=ocr-data /tessdata/*.traineddata /usr/share/tesseract-ocr/5/tessdata/
COPY --from=ocr-data /tessdata/script /usr/share/tesseract-ocr/5/tessdata/script
COPY --from=ocr-data /tessdata/LICENSE /opt/stirling/tessdata-LICENSE
COPY entrypoint.sh /usr/local/bin/edsys-stirling
COPY healthcheck.py /usr/local/bin/edsys-stirling-health
RUN chmod 0444 /app/app.jar && chmod 0555 /usr/local/bin/edsys-stirling /usr/local/bin/edsys-stirling-health \
    && find /scripts -type f -name '*.sh' -exec chmod 0555 {} + \
    && ln -s /configs /app/configs && ln -s /logs /app/logs \
    && ln -s /customFiles /app/customFiles && ln -s /pipeline /app/pipeline \
    && ln -s /storage /app/storage && fc-cache -f
# Seed an immutable, empty office profile before any document is processed.
RUN mkdir -p /var/lib/libreoffice-template && chown 1000:1000 /var/lib/libreoffice-template \
    && setpriv --reuid=1000 --regid=1000 --clear-groups env \
       HOME=/var/lib/libreoffice-template STIRLING_LO_SANDBOX=off \
       timeout 60 soffice --headless --norestore --terminate_after_init \
       -env:UserInstallation=file:///var/lib/libreoffice-template \
    && test -d /var/lib/libreoffice-template/user \
    && chown -R root:root /var/lib/libreoffice-template && chmod -R a+rX,go-w /var/lib/libreoffice-template
# Upstream OCR selectors enumerate only the root directory, not script/.
# Immutable aliases expose every script model without duplicating model bytes.
RUN for model in /usr/share/tesseract-ocr/5/tessdata/script/*.traineddata; do \
      model_name="$(basename "$model")"; \
      ln -s "script/$model_name" "/usr/share/tesseract-ocr/5/tessdata/script_$model_name"; \
    done
COPY --chmod=0444 --from=backend /patch/patched.jar /app/app.jar
COPY --chmod=0444 --from=backend /patch/url-context-patch.json /opt/stirling/url-context-patch.json
ENV PATH=/opt/venv/bin:/opt/java/openjdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    HOME=/home/stirlingpdfuser TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    TMPDIR=/tmp/stirling-pdf TMP=/tmp/stirling-pdf TEMP=/tmp/stirling-pdf \
    STIRLING_TEMPFILES_DIRECTORY=/tmp/stirling-pdf STIRLING_TEMPDIR=/tmp/stirling-files \
    SAL_USE_VCLPLUGIN=svp SAL_DISABLE_PRINTERLIST=1 OOO_FORCE_DESKTOP=none \
    DBUS_SESSION_BUS_ADDRESS=/dev/null MALLOC_ARENA_MAX=2 \
    DISABLE_ADDITIONAL_FEATURES=true VERSION_TAG=3.2.2 \
    OMP_THREAD_LIMIT=4 STIRLING_LO_SANDBOX=required
USER 1000:1000
EXPOSE 8080
ENTRYPOINT ["tini", "--", "/usr/local/bin/edsys-stirling"]
LABEL org.opencontainers.image.title="EdSys Stirling PDF Core" \
      org.opencontainers.image.version="3.2.2-core-edsys6" \
      org.opencontainers.image.source="https://github.com/Stirling-Tools/Stirling-PDF/tree/v3.2.2" \
      org.opencontainers.image.licenses="MIT" \
      com.edsys.stirling.flavor="core-no-processor"
