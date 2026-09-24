# Firefox Stack

A locked down Firefox, streamed to the browser, on the public domain. The LinuxServer
image runs Firefox on a Wayland compositor (labwc) and streams it with Selkies.

Video, audio and input all travel over one WebSocket on the page's own HTTPS
connection, so the stream follows the normal public path (relay, external gateway,
middlewares) and needs no extra port. WebRTC based alternatives (e.g. neko) need a
UDP or TCP media port the browser dials directly, which the relay does not carry.

The profile lives on the image's anonymous volume, so every redeploy starts clean and customization only happens through the docker config files.

## Access

- **Authentik forward auth** on the router, restricted to the user group.
- **nginx basic auth** inside the container, on every path including the stream's
  WebSocket. Authentik injects the header, so users never see a prompt.

## Display and GPU

The Intel Arc B50 encodes the stream (VA-API H.264, detected by the image).

The `xe` driver ignores the stride on widths that are not a multiple
of 16 and the stream shears diagonally, so the resolution is forced
to aligned sizes (linuxserver/docker-baseimage-selkies#160).
A new connection starts at the client's exact size for a moment before it is realigned.

WebKit clients (Safari, and every browser on iOS) are redirected to `/?offscreen_worker=false`.
By default the web client shows the stream there through a `<video>` element it starts only once,
and Safari's "Never Auto-Play" refuses that start silently, leaving a black screen.
The parameter selects the page canvas path which needs no playback and costs clients some CPU per frame.
Chromium and Firefox keep the default path.

## Lockdown and Hardening

Each layer covers what the one before cannot.

| Layer | Delivered as | Covers |
|---|---|---|
| Image hardening env | environment | Sudo, terminals, `xdg-open`, the remote command channel, sharing, gamepads, the Selkies sidebar |
| Compositor template | Docker config over the image's labwc template | One maximized, undecorated window that cannot be moved, resized, minimized or closed |
| Enterprise policies | `policies.json` | Extensions, profiles, private browsing, AI features, telemetry, backup, history, new tab content, homepage, menu bar, DoH, the download directory |
| Distribution file | `distribution.ini` | Replaces the Ubuntu package's copy, which creates bookmarks on every new profile |
| Autoconfig | `autoconfig.js` and `mozilla.cfg` | Toolbar and new tab customization, the app and tab list menus, and the content filter below |

Compositor template: the image's own hardening seds comment out a range that runs from
each disabled keybind to the next closing tag, which swallows the rest of its template
and leaves labwc without window rules. The replacement template contains none of the
lines those seds match. `NO_DECOR` must stay unset: it switches server decorations off
and GTK then draws its own window buttons again.

Autoconfig: the hiding stylesheet is registered as a user sheet, because Firefox ignores
`@-moz-document` blocks in agent sheets and without them the rules would hit ordinary
web pages. The content filter is a content policy that rejects:

- every about page outside an allowlist (new tab and home, plus the pages the browser
  needs itself: blank, srcdoc and the error pages);
- chrome, resource, moz-src, file and jar documents loaded into a tab, and all of
  view-source, since those reach the same internal pages and the container's
  filesystem;
- every request of any type to a private, loopback, link local, CGNAT, reserved or
  multicast address, or to `localhost`.

`WebsiteFilter` cannot do the last part: its match patterns take exact hosts, not
address ranges. Names are covered by DNS instead. DoH is the only resolver, with no
fallback, and it drops answers that point at private addresses, so a public name that
resolves into the LAN does not load either.

## Files

Uploads come in by dragging a file onto the stream (the sidebar is hidden, so there is
no other way, and none on a phone). They land in `~/Desktop`, where a website's file
picker finds them. Downloads are locked to the same folder. It is a tmpfs of 256 MB
shared by both directions: the size cap is the only limit, and the folder is emptied on
every restart. Nothing leaves the session: Selkies downloads are off, `file://` is
blocked, and the container has nothing that could open a downloaded file.

## Known gaps

- The basic auth password is readable from inside the session: the secret is mounted
  world readable, s6 writes a world readable copy to
  `/run/s6/container_environment/PASSWORD` and exports it into the process environment,
  so a website's upload button can pick it. Only authenticated users can reach it, and it
  only protects a port behind Authentik.
