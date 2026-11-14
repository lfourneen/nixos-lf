# Managed Firefox

English | [简体中文](CN/browser_CN.md) | [Documentation](README.md)

## Package and launch

Firefox is a native Nix package. [firefox.nix](../home/programs/firefox/firefox.nix) binds the binary, enterprise policies, AutoConfig and fixed fonts into one immutable store closure; Home Manager owns the separate `~/.config/mozilla/firefox/lfour` profile. Updates follow the locked Nixpkgs input and a rebuilt generation, with Firefox's binary updater disabled. Existing Flatpak profiles are not imported or deleted.

The public launcher runs [guard.py](../home/programs/firefox/guard.py) before opening the explicit managed profile. In B it requires fresh same-generation mode/DNS/relay/verification state, passive listener identity checks and live API invariants through the desktop's GUI forwarder. The root supervisor verifies the private configuration snapshot and internal controller; the browser uses its published readiness without reading those private files. Daily Firefox admits A and healthy B; identity requires healthy B, and the portal entry admits only explicit A. C, unknown and `unenforced` refuse launch. A second state read rejects a transition during preflight; the relay and kernel policy handle changes after launch.

The launcher checks fonts under strict privacy and starts the browser in the outer sandbox described below. Host-side instance records use process start times and package identity to reject reuse of an older package; namespace PIDs in stale profile locks do not identify desktop processes. Desktop/MIME entries use it; former Zen/Chrome entry IDs redirect to Firefox. Policy changes require a full browser restart. `firefox --check` inspects prerequisites without creating the profile or opening a window.

## Outer sandbox

[sandbox.py](../home/programs/firefox/sandbox.py) retains the desktop UID; strict browsers also get an isolated network namespace, while bubblewrap creates separate mount, user, PID, IPC and UTS namespaces. The browser sees a synthetic home and a private `/tmp` and `/dev`. Capabilities are dropped, inherited environment variables are replaced by an allow-list, and Gecko's content-process sandbox remains enabled. The native Firefox binary is reused; the Nix wrapper and small [scope helper](../home/programs/firefox/scope.c) supply the outer policy without a Gecko source patch.

| Resource | Browser access |
|---|---|
| Persistent files | Read/write managed profile, `~/.cache/firefox-managed` and `~/Downloads`; other personal files require a file-chooser grant |
| Runtime and trust | Read-only Nix store, selected resolver/CA files and graphics driver; no host root filesystem or general home/runtime-directory mount |
| Display and audio | Named Wayland socket, optional Pulse socket and DRM render nodes; configured NVIDIA access also exposes matching primary nodes and rendering interfaces. No X11 socket, input devices, camera or NVIDIA modeset interface |
| Session bus | Filtered proxy: file chooser, read-only settings, request/session close, scheme-support query and remoting for this exact profile |
| Other host interfaces | No system bus, SSH/GPG agents, container or core-controller sockets; Landlock prevents connecting to host abstract Unix sockets and signalling outside the scope |

System certificate trust uses the NixOS CA bundle at `/etc/ssl/certs/ca-certificates.crt`, including declared system CAs. The launcher resolves its symlink and mounts the actual file read-only at that path: NSS/p11-kit can read it without exposing `/etc/static` or the full host certificate directory. A missing bundle refuses launch. Certificate verification remains enabled.

`my.firefox.nvidiaAcceleration` defaults to the NixOS NVIDIA driver selection. NVIDIA's userspace renderer needs its matching DRM primary nodes, numbered `/dev/nvidia*` devices, `nvidiactl` and `nvidia-uvm`, plus the read-only EGL external-platform configuration. The launcher uses host sysfs only to associate primary nodes with NVIDIA render devices; neither the full `/sys` nor `/dev` is mounted. Other GPUs' primary nodes, `nvidia-modeset`, UVM tools and capability devices remain hidden. Setting this option to `false` keeps render-node-only device access. Gecko sandboxes remain active, and the locked preference continues to disable WebGL.

The syscall filter also blocks keyring access, process inspection, file-handle opens and selected unused kernel interfaces. It leaves the namespace and seccomp operations needed by Gecko available. Landlock ABI 6 or newer and unprivileged user namespaces are required. Missing confinement or a failed bus proxy refuses or terminates the browser; there is no unconfined fallback.

The bus proxy runs in its own minimal mount namespace. Read-only `/.flatpak-info` and per-instance host PID metadata let the desktop portal recognize `org.nixos.ManagedFirefox` as sandboxed, using its existing Flatpak identity protocol. This does not install a Flatpak application or runtime. Only `doc/by-app/org.nixos.ManagedFirefox` is mounted as the browser's document tree, so grants for other applications remain hidden. The locked file picker uses the portal: selecting a personal file exposes that document without exposing its parent directory. Files outside the mounted directories cannot be opened by passing a raw host file path alone. Writable-directory symlinks are refused.

The normal launcher can open another link in the running browser through its exact profile-specific D-Bus name. Generation records and a host launch lock stay outside the browser mounts. Close all old windows before opening a rebuilt package; the managed profile persists across that restart. `firefox --check` also checks the private runtime directory, desktop sockets and Landlock support without opening a window.

## Network policy and identities

`my.firefox.dailyRequireProxy` defaults to `false`, independently of `my.proxy.strictBrowser`. The default daily browser uses Gost `33332`: A is direct access with the local network's public IP; healthy B uses the core's subscription routing, which can include DIRECT. A failed proxy does not select A automatically. The outer filesystem sandbox, TLS and privacy settings apply in both modes. The old `my.firefox.requireProxy` option is a compatibility alias for the daily option only.

`firefox-identity` always requires the strict inlet enabled by `my.proxy.strictBrowser=true`; setting `dailyRequireProxy=true` also selects it for the daily browser. Each strict browser has loopback networking only, with an in-namespace HTTP relay at `127.0.0.1:18080` connected to the systemd-created Unix socket. `clash-strict.socket` installs `/run/clash-strict/proxy.sock` for the desktop user; `--check` and launch both validate its presence, type and ownership. The host relay accepts streams only while shared readiness reports healthy B and aborts existing streams on lost readiness. Its 100 ms poll is an additional bound after the shared state changes, not a zero-delay failure detector. The relay authenticates the established server socket UID before forwarding browser bytes, and A/C cut strict-inlet traffic in the nft transaction. No arbitrary host TCP service, native DNS, LAN or direct Internet socket is reachable from that namespace.

The core’s pinned `lf-strict` inlet uses the dedicated remote-node group and rejects actual DIRECT/COMPATIBLE leaves and private targets, including after GUI group or Global selection. The ordinary desktop retains Rule routing. Socket-queued bytes and data already at a remote peer cannot be recalled. Other unconfined processes sharing the desktop UID remain trusted and can invoke the package-private binary.

The daily browser can instead select the transparent ECH path with `dailyRequireProxy=false`; it retains host A/B/C networking and has no private network namespace. This choice does not change identity or portal networking.

Daily Firefox and `firefox-identity` enforce HTTPS-only, reject certificate/security-warning bypass and lock phishing, malware and download protection. The identity package installs no extensions and uses separate profile/cache/download/document-portal identity. No existing cookies, accounts or bookmarks are copied. Safe Browsing updates and reputation queries may disclose query/download metadata; see [Mozilla’s protection explanation](https://support.mozilla.org/en-US/kb/how-does-phishing-and-malware-protection-work). A valid certificate does not authenticate a site’s business identity. HTTPS-only retains browser exception semantics; it is not a promise that all possible HTTP bytes are prohibited.

`firefox-portal URL` accepts one literal-IP HTTP(S) address on port 80/443 in explicit A. Its separate temporary profile has no extensions or existing credentials; its network namespace can reach only a single-address relay. It expires after 15 minutes. The daily browser disables its automatic plaintext captive-portal probe; system NetworkManager connectivity detection remains separate. It does not silently change profiles or bypass policy when a portal appears.

Use the daily profile for ordinary browsing and `firefox-identity` for selected accounts. Keeping a profile separate reduces state mixing but does not anonymize a logged-in account or defeat flow correlation. Anonymous activity needs a separately selected supported anonymity client and its own update/exit assessment; no Tor client or multihop claim is enabled by these entries.

WebRTC, HTTP/3 and browser DoH remain disabled. Explicit proxy name resolution happens at the core and uses the common suffix/target checks; DNSSEC and encrypted transport retain their separate [DNS boundaries](dns.md).

## ECH discovery and proxy compatibility

ECH encrypts the inner TLS ClientHello, including the origin SNI. In this configuration, the browser obtains its ECHConfig from an HTTPS DNS record. GREASE adds a synthetic ECH-shaped extension; a real ECH handshake uses the server's ECHConfig to encrypt ClientHelloInner.

### Browser paths

| `my.firefox.networkPath` | Name discovery and transport | ECH behavior |
|---|---|---|
| `"http-proxy"`, default | Strict local relay or compatibility Gost HTTP proxy; target resolution is delegated to the core | The pinned Firefox 160.0 excludes HTTPS CONNECT from HTTPS-RR discovery. GREASE remains enabled; HTTPS-RR discovery uses the transparent path below |
| `"transparent"` | System DNS and native HTTPS-RR queries; A uses direct sockets, B uses the host TUN/firewall path | NSS can use the returned ECHConfig and perform a real ECH handshake with a compatible origin |

Firefox's [connection gate](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/protocol/http/nsHttpChannel.cpp) checks both the proxy DNS strategy and `UsingConnect()`. Changing DNS transport alone does not change this gate. This package keeps the test-only `force_use_https_rr` override disabled; the transparent option uses the packaged browser without a Gecko source patch.

Select it explicitly in the system configuration:

```nix
my.proxy.tunMode = true;
home-manager.users.lfour.my.firefox.dailyRequireProxy = false;
home-manager.users.lfour.my.firefox.networkPath = "transparent";
```

Rebuild the generation and fully restart Firefox. The module rejects transparent operation with `tunMode=false`. The network path is selected when building the package. The same launcher still checks network readiness; C blocks traffic in the kernel and refuses new launches. A keeps literal HTTP portal access. `bootMode`, `proxyOnly`, browser fingerprinting protection and `dnsEncryption` remain independent choices.

### Where discovery happens

The normal encrypted public-DNS path is shown below; DNS recovery and private resolver exceptions follow the [DNS policy](dns.md).

```text
Firefox native HTTPS query
  -> libc resolver -> systemd-resolved -> dnsmasq suffix filter
       A: Unbound -> authenticated DoT + local DNSSEC validation
       B: core suffix filter -> configured encrypted resolver
  <- HTTPS RR carrying ECHConfig

Firefox/NSS builds the encrypted ClientHello
  -> A: direct TCP
  -> B: managed TUN/core -> Rule DIRECT or selected proxy -> origin
```

The Unix browser uses the [native resolver API](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/dns/PlatformDNSUnix.cpp) for type 65. Browser DoH remains disabled and locked. No additional local DoH endpoint is needed: the existing resolver chain transports HTTPS records as DNS messages.

The core retains fake-IP address behavior while forwarding HTTPS and SVCB records through its normal resolver. The shared filter runs first, so a blocked domain receives local NXDOMAIN instead of an ECHConfig. Record parameters, including the ECH public name, are carried back to the browser; the core does not consume the browser's ECHConfig or terminate its origin TLS session. A's DNSSEC validation, B's encrypted transport, approved split DNS and MagicDNS retain their [separate boundaries](dns.md#responsibilities).

NSS selects the config, constructs ClientHelloInner/Outer, verifies the certificate and handles authenticated retry configs. GREASE and `ECHAccepted` are different observations: a service accepting ordinary TLS can see GREASE while reporting that ECH was not accepted.

### Routing and failure behavior

Managed core sniffing is disabled in both Rule and uniform proxy modes. An ECH outer SNI is a cover name and cannot replace the requested target. Explicit proxy targets and fake-IP mappings supply names for domain routing; literal IP traffic follows IP rules. The root parser applies this policy to cached GUI profiles too.

When no usable HTTPS/ECH record exists, ordinary certificate-verified TLS remains available. ECH is used for sites with a usable configuration. `network.dns.echconfig.fallback_to_origin_when_all_failed=false` disables the origin fallback controlled by that preference, but does not prohibit NSS's separate authenticated retry without ECH. A trusted server rejecting ECH with no usable retry keys can cause a new ordinary TLS connection carrying the origin SNI. Authenticated key retries remain allowed; an untrusted rejection or invalid certificate does not authorize this retry. Missing/stripped records, DNS failure and authenticated rejection therefore have different outcomes. `dnsEncryption="strict"` governs DNS transport and does not require every site to support ECH.

An explicit HTTP proxy can see CONNECT targets, including when another client supports ECH through that proxy. The native path's local core can see DNS/fake-IP mappings and routing targets. ECH does not hide destination IPs, traffic timing, accounts or the content visible to the origin. See [RFC 9849](https://www.rfc-editor.org/rfc/rfc9849.html) for the protocol's acceptance and retry rules.

### Maintaining the integration

`browser-ech-vm` runs the selected production Firefox packages inside the outer sandbox with the real helper/core with a local DNS fixture, temporary CA, ECH server and a different address for the cover name. It distinguishes accepted ECH from ordinary TLS/GREASE, key retry, authenticated retry without ECH, untrusted rejection and certificate failure. It checks that cover SNI does not retarget the connection and retains A portal/C launch checks. It uses fresh profiles and never attaches to a personal browser.

Run `browser-ech-vm` and `browser-ech-uniform-vm` alongside `browser-sandbox-vm`, `browser-privacy`, `proxy-core` and `proxy-profile` after browser or core changes. Both routing policies exercise actual locked preferences and both browser network paths. Check the browser's CONNECT gate, native resolver and retry handling together with the core's fake-IP service-record handling. Use the [source compatibility checks](maintenance.md#source-compatibility-checks) and keep these path descriptions aligned with the selected versions.

## Privacy settings

`strictPrivacy` is enabled in the module. AutoConfig `lockPref` covers preferences beyond enterprise policy lock support and resists conflicting `user.js` values for every profile opened with the package.

| Layer | Behavior and boundary |
|---|---|
| Fingerprinting | RFP and letterboxing reduce Canvas, timezone, hardware and window-size signals; customization still differs from Tor Browser |
| Tracking and state | Strict protection, FPP and Total Cookie Protection reduce scripts and cross-site state correlation |
| URLs and profiling | Query stripping, bounce cleanup, cross-origin referrer restrictions and GPC; suggestions, speculation, telemetry, studies, sponsored content, Sync and built-in IP Protection disabled |
| APIs and fonts | WebRTC, WebGPU, geolocation, battery and sensors disabled; strict privacy also disables WebGL and restricts visibility to fixed DejaVu/Noto/CJK/emoji fonts |
| ECH / TLS | Native HTTPS records, ECH and GREASE enabled; transparent mode supports ECH discovery, while the pinned browser's default CONNECT path does not |
| Exit address | Shared A/B/C and routing policy; default B Rule routing selects DIRECT or a proxy node |

Strict extension policy blocks undeclared extensions. uBlock Origin is force-installed and KISS Translator is removable. Signed packages/updates come from Mozilla Add-ons through the selected network path and require connectivity; package policy and Gecko protections do not depend on extension download completing.

Gecko's content-process sandbox and the outer policy protect different layers. The outer policy constrains the native parent and its descendants; privacy preferences constrain browser behavior. Neither changes the routing or resolver trust boundaries above.

Package integration and upgrade checks are covered in [Patch maintenance](maintenance.md); resolver behavior is covered in [DNS](dns.md).
