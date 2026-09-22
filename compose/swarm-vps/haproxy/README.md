# Relay

HAProxy on the relay VPS, the only thing on this infrastructure that faces the internet.
It owns public 443 and forwards every client connection to the external gateway at home
with a PROXY protocol v2 header. A standalone Compose project, not a Swarm stack: deployed
with `compose:deploy swarm-vps/haproxy` from the operator's machine, which drives the VPS
daemon over ssh and builds both images there.

- **TCP only, nothing terminated.** The public frontend is `mode tcp`; the client's TLS
  session runs end to end to Traefik and this host cannot read what it relays.
- **Two backends, HAProxy picks.** `home` is a direct TCP connection per client over the
  WAN (the router DNATs 8443 from this host's address to the gateway node). `tunnel` is
  the WireGuard path to the router, `backup` only. Health checks complete a TLS handshake
  with Traefik through the same PROXY path, so a broken DNAT or a dead gateway reads as
  DOWN within `fall 3` intervals and clients are refused promptly instead of timing out.
- **No address of this host in the config.** Listeners bind by interface
  (`SO_BINDTODEVICE`): public 443 on `eth0` only, the metrics endpoint on `wg0` and
  loopback only. Dev and prod deploy the same directory; the mise profile picks the host.
- **Home address from WireGuard, no DDNS.** `home` is declared on a placeholder address
  and `disabled`. The `endpoint` sidecar reads the router's current endpoint off the
  WireGuard peer (authenticated by the peer key, follows a public IP change) and sets it
  through HAProxy's runtime API, then enables the server. It polls every `INTERVAL_INIT`
  seconds until HAProxy carries a real address (a recreated container starts on the
  placeholder), then every `INTERVAL` seconds. No reload is involved.
- **Config baked into the image.** `build/haproxy/` is the stock image plus
  `haproxy.cfg`; nothing is copied to the host and the container stays `read_only`. A
  config change is a container recreate: HAProxy soft-stops for up to
  `stop_grace_period`, existing streams finish, new connections take the tunnel until the
  sidecar re-arms `home` a second after the new container starts.
- **Logs stay on the host.** `docker logs relay-haproxy` is the only record of which path
  a client took (`relay_out/home` or `relay_out/tunnel` on each `relay_in` line) and of
  server state changes. Healthcheck and scrape requests are not logged
  (`option dontlog-normal`); denies and errors are. Nothing ships these logs off the VPS.
- **Metrics on the tunnel.** The Prometheus endpoint answers on the tunnel address only.
  The metrics stack scrapes it over the tunnel (`relay` job, target file chosen per
  environment) and Gatus alerts when the direct path is DOWN, since the tunnel hides that
  from users. See the metrics stack README.
