FROM ghcr.io/dgtlmoon/changedetection.io:0.60.10@sha256:72f97681f390e4d6ae3e6585e1e0036449910f347144b766fc23910458566a1a
USER root
COPY --chmod=0644 patch_app.py /opt/edsys/patch_app.py
COPY --chmod=0644 app_guard.py /app/changedetectionio/edsys_guard.py
COPY --chmod=0644 egress_proxy.py /opt/edsys/egress_proxy.py
RUN chmod 0755 /opt/edsys && python /opt/edsys/patch_app.py && python -m compileall -q /app/changedetectionio/edsys_guard.py /opt/edsys/egress_proxy.py
USER 1000:1000
