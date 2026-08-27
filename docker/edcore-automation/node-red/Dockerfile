FROM docker.io/nodered/node-red:4.1.13-22@sha256:7d8a2bc628f80d2efc9a21832eac7c579a2c9ea4cda9040d3b0d4d68a1e36bc4

USER root
COPY entrypoint.sh settings.js healthcheck.js /opt/edsys/
COPY project-seed/ /opt/edsys/project-seed/
RUN chmod 0755 /opt/edsys/entrypoint.sh \
    && chmod 0644 /opt/edsys/settings.js /opt/edsys/healthcheck.js \
    && chown -R node-red:node-red /opt/edsys

USER node-red
ENTRYPOINT ["/opt/edsys/entrypoint.sh"]
