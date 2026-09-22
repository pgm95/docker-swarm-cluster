# Relay HAProxy: the stock image plus this stack's config. Built on the target
# daemon, so the config travels inside the build context over ssh and nothing
# is copied onto the host; the container stays read-only.
# Pinned to the amd64 manifest of haproxy:3.0-alpine (3.0.27 at pin time).
FROM haproxy:3.0-alpine@sha256:33ed3a2901ebe16417a0df1eb1a9de72010f851b6786ba00335ccd80da1f2225
COPY haproxy.cfg /usr/local/etc/haproxy/haproxy.cfg
