# Endpoint sidecar: `wg` to read the router peer's current endpoint, `socat` to
# talk to HAProxy's admin socket. Nothing else.
FROM alpine:3.21@sha256:f27cad9117495d32d067133afff942cb2dc745dfe9163e949f6bfe8a6a245339
RUN apk add --no-cache wireguard-tools-wg socat
COPY endpoint.sh /usr/local/bin/endpoint
RUN chmod 0555 /usr/local/bin/endpoint
ENTRYPOINT ["/usr/local/bin/endpoint"]
