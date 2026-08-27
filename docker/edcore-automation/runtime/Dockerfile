FROM docker.io/library/python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --require-hashes -r /tmp/requirements.txt \
    && groupadd --gid 10001 automation \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin automation \
    && install -d -o 10001 -g 0 -m 0770 /var/lib/automation-runtime /var/lib/automation-event-harness \
    && rm /tmp/requirements.txt

COPY --chown=10001:0 src/ /app/src/
COPY --chown=10001:0 config/ /app/config/

USER 10001:0
WORKDIR /app
CMD ["python", "-m", "automation_runtime.service"]
