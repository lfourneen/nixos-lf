# Managed Firefox

English | [简体中文](CN/browser_CN.md) | [Documentation](README.md)

## Package and launch

Firefox is a native Nix package. [firefox.nix](../home/programs/firefox/firefox.nix) binds the binary, enterprise policies, AutoConfig and fixed fonts into one immutable store closure; Home Manager owns the separate `~/.config/mozilla/firefox/lfour` profile. Updates follow the locked Nixpkgs input and a rebuilt generation, with Firefox's binary updater disabled. Existing Flatpak profiles are not imported or deleted.

The public launcher runs [guard.py](../home/programs/firefox/guard.py) before opening the explicit managed profile. In B it requires fresh same-generation mode/DNS/relay/verification state, passive listener identity checks and live API invariants through the desktop's GUI forwarder. The root supervisor verifies the private configuration snapshot and internal controller; the browser uses its published readiness without reading those private files. A permits opening a literal HTTP captive portal without working public DNS. C, unknown and `unenforced` refuse launch. A second state read rejects a transition during preflight; the relay and kernel policy handle changes after launch.

The launcher checks fonts under strict privacy, forces Wayland, removes inherited sandbox-disabling switches and rejects reuse of an older Firefox package holding the profile lock. Desktop/MIME entries use it; former Zen/Chrome entry IDs redirect to Firefox. Policy changes require a full browser restart. `firefox --check` inspects prerequisites without creating the profile or opening a window.

## Network policy

HTTP/HTTPS proxy settings are locked to Gost at `127.0.0.1:33332`, with empty bypass/PAC/SOCKS settings, localhost proxying enabled and direct failover disabled. A passes through, B reaches Mihomo's routing policy, C closes the application path. Browser privacy does not change `bootMode` or `proxyOnly`.

WebRTC and HTTP/3 are disabled, keeping web traffic on the TCP relay. DoH is disabled and locked (`network.trr.mode=5`), so browser-originated DNS uses the managed system resolver. Explicit HTTP proxy requests can delegate target resolution to Gost/Mihomo; the core applies the same domain snapshot, additions and exceptions before DNS responses and application dials. DNSSEC and encryption depend on the selected resolver path and `dnsEncryption`, as described in [DNS](dns.md). Captive-portal detection and NetworkManager connectivity checks remain enabled; local HTTP remains available under HTTPS-First.

## Privacy settings

`strictPrivacy` in the module defaults to true. AutoConfig `lockPref` covers preferences beyond enterprise policy lock support and resists conflicting `user.js` values for every profile opened with the package.

| Layer | Behavior and boundary |
|---|---|
| Fingerprinting | RFP and letterboxing reduce Canvas, timezone, hardware and window-size signals; customization still differs from Tor Browser |
| Tracking and state | Strict protection, FPP and Total Cookie Protection reduce scripts and cross-site state correlation |
| URLs and profiling | Query stripping, bounce cleanup, cross-origin referrer restrictions and GPC; suggestions, speculation, telemetry, studies, sponsored content, Sync and built-in IP Protection disabled |
| APIs and fonts | WebRTC, WebGPU, geolocation, battery and sensors disabled; strict privacy also disables WebGL and restricts visibility to fixed DejaVu/Noto/CJK/emoji fonts |
| ECH / TLS | ECH discovery, native HTTPS records and GREASE enabled; a real handshake still requires a usable ECHConfig and compatible origin/DNS/proxy path |
| Exit address | Shared A/B/C and routing policy; default B Rule routing can still choose DIRECT |

Strict extension policy blocks undeclared extensions. uBlock Origin is force-installed and KISS Translator is removable. Signed packages/updates come from Mozilla Add-ons through the relay and require connectivity; package policy and Gecko protections do not depend on extension download completing.

GREASE is not proof of ECH. The relay/core can see an HTTP CONNECT target; ECH does not hide destination IPs, accounts or timing. These settings do not prevent account correlation, exit reputation, protocol classification or traffic analysis.

Gecko's content-process sandbox remains enabled. The native parent retains ordinary desktop-user filesystem/socket access. Same-UID programs and the package-private unchecked binary can bypass launcher preflight; this is not an outer filesystem or session-bus sandbox. `strictPrivacy=false` relaxes fingerprinting, font, WebGL and extension restrictions while preserving the managed relay, system DNS and launch prerequisites.

Package integration and upgrade checks are covered in [Patch maintenance](maintenance.md); resolver behavior is covered in [DNS](dns.md).
