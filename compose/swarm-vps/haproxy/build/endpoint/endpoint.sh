#!/bin/sh
# Keeps HAProxy's `home` server pointed at the home public address.
#
# WireGuard learns the router's public address from every handshake (roaming),
# so `wg show <if> endpoints` is an authenticated, always current source for it.
# Whenever it differs from what HAProxy has, set it through the runtime API.
# No reload: `set server addr` is live and existing connections are untouched.
# The server starts disabled on a placeholder address; the first update also
# enables it. HAProxy only accepts an address of the same family as the one in
# the config, which is why the placeholder is an IPv4 address and not none.
#
# Polls every INTERVAL_INIT seconds until HAProxy carries a real address (a
# fresh container starts on the placeholder, so the direct path is down until
# this loop runs), then every INTERVAL seconds to follow a roaming home address.
set -eu

SOCK=/run/haproxy/admin.sock
WG_INTERFACE=${WG_INTERFACE:-wg0}
HOME_PORT=${HOME_PORT:-8443}
BACKEND=${HAPROXY_BACKEND:-relay_out}
SERVER=${HAPROXY_SERVER:-home}
INTERVAL=${INTERVAL:-10}
INTERVAL_INIT=${INTERVAL_INIT:-1}

log() { printf '%s endpoint: %s\n' "$(date -u +%FT%TZ)" "$*"; }

hap() { printf '%s\n' "$1" | socat -t 3 - "UNIX-CONNECT:$SOCK"; }

wg_endpoint() {
    # First peer only: the relay has exactly one peer, the router.
    wg show "$WG_INTERFACE" endpoints 2>/dev/null \
        | awk 'NR==1 && $2 != "(none)" { sub(/:[0-9]+$/, "", $2); print $2 }'
}

haproxy_addr() {
    # "show servers state" columns: be_id be_name srv_id srv_name srv_addr ...
    hap "show servers state $BACKEND" 2>/dev/null \
        | awk -v s="$SERVER" '$4 == s { print $5 }'
}

log "watching $WG_INTERFACE for $BACKEND/$SERVER, port $HOME_PORT, every ${INTERVAL_INIT}s until set, then every ${INTERVAL}s"
delay=$INTERVAL_INIT
while :; do
    if [ -S "$SOCK" ]; then
        ip=$(wg_endpoint || true)
        cur=$(haproxy_addr || true)
        if [ -n "$ip" ] && [ "$ip" != "$cur" ]; then
            if hap "set server $BACKEND/$SERVER addr $ip port $HOME_PORT" >/dev/null \
               && hap "set server $BACKEND/$SERVER state ready" >/dev/null; then
                log "home is now $ip (was ${cur:-unset})"
                cur=$ip
            else
                log "failed to set $ip on $BACKEND/$SERVER"
            fi
        fi
        # Placeholder gone (set by us or already there): settle into the slow poll.
        # A missing or unreadable state keeps the fast poll, since the address is unknown.
        case "$cur" in
            ""|0.0.0.0) delay=$INTERVAL_INIT ;;
            *) delay=$INTERVAL ;;
        esac
    fi
    sleep "$delay"
done
