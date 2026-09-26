FROM ghcr.io/idisposable/docker-wyze-bridge@sha256:708903d2882c76966ecb29763a075372d89773aa6796d0aab70f04369cc6d521

RUN apk add --no-cache iptables=1.8.11-r1

COPY entrypoint-firewalled.sh /usr/local/bin/entrypoint-firewalled
RUN chmod 0755 /usr/local/bin/entrypoint-firewalled

ENTRYPOINT ["/usr/local/bin/entrypoint-firewalled"]
