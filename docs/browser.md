# Managed Firefox

English | [简体中文](CN/browser_CN.md) | [Documentation](README.md)

## Package and launch

Firefox is packaged natively in Nix: [firefox.nix](../home/programs/firefox/firefox.nix) locks the binary, enterprise policies, AutoConfig, and a fixed set of fonts into a single immutable store closure, while Home Manager manages the `~/.config/mozilla/firefox/lfour` profile. Updates come from bumping the locked Nixpkgs input and rebuilding the system, with Firefox's built-in updater disabled; any old Flatpak profiles are left untouched.

Before opening the managed profile, the launcher runs [guard.py](../home/programs/firefox/guard.py). In **B**, it checks that mode, DNS, relay, and verification states are fresh and on the same generation, then verifies listener identities and the live controller policy using the desktop controller socket (selecting the fast check matching the active `rules` or `proxy-only` policy when runtime routing is enabled, while root services handle validating private config snapshots).

**C**, unknown state, and `unenforced` all block startup. At the end of preflight, the launcher re-verifies the policy generation along with the routing and exit epochs—opening a link in an already-running instance goes through the exact same checks, and once Firefox is up, the relay and firewall enforce any later state changes.

Next, the launcher checks fonts under strict privacy and starts the browser inside the outer sandbox below. Host-side instance records track process start times and package identity so a new launch won't accidentally attach to a stale package, and namespace PIDs in stale profile locks are never treated as host desktop PIDs. Desktop and MIME entries all point here (former Zen/Chrome entry IDs redirect to Firefox). Policy changes require a full browser restart; if you only want to check prerequisites without creating a profile or opening a window, run `firefox --check` or `firefox-identity --check`.

## Outer sandbox

[sandbox.py](../home/programs/firefox/sandbox.py) keeps the desktop UID while using `bubblewrap` to create isolated mount, user, PID, IPC, and UTS namespaces (and an isolated network namespace for strict browsers). Inside, Firefox sees a synthetic home directory plus private `/tmp` and `/dev`, with capabilities dropped and inherited environment variables replaced by an allowlist, while Gecko's own content-process sandbox stays enabled. The native Firefox binary itself is untouched; the Nix wrapper and small [scope helper](../home/programs/firefox/scope.c) supply the outer confinement without patching Gecko.

| Resource | Browser access |
|---|---|
| Persistent files | Read/write access to the entry's managed profile, cache, and download directory; other personal files require a file-chooser grant |
| Runtime and trust | Read-only Nix store, selected resolver/CA files, and graphics driver; no host root filesystem or general home/runtime-directory mount |
| Display and audio | Named Wayland socket, optional Pulse socket, and DRM render nodes (plus matching primary nodes and rendering interfaces when NVIDIA acceleration is enabled); no X11 socket, input devices, camera, or NVIDIA modeset interface |
| Session bus | Filtered proxy allowing only file chooser, read-only settings, request/session close, scheme-support queries, and remoting for this exact profile |
| Other host interfaces | No system bus, SSH/GPG agents, container sockets, or core-controller sockets; Landlock blocks host abstract Unix sockets and signalling outside the scope |

### Certificates and graphics

System certificate trust uses the NixOS CA bundle at `/etc/ssl/certs/ca-certificates.crt` (including declared system CAs). The launcher resolves the symlink and bind-mounts the actual file read-only at that path so NSS/p11-kit can read it without exposing `/etc/static` or the rest of the host certificate directory. If the bundle is missing, launch is refused, and certificate verification always stays on.

`my.firefox.nvidiaAcceleration` follows the declared NVIDIA driver by default, exposing the GPU nodes and read-only EGL configuration needed for rendering while hiding unrelated devices and modesetting interfaces. Set it to `false` to restrict access to render nodes only; Gecko confinement stays on, and strict privacy still disables WebGL.

### System interfaces and file access

The syscall filter blocks keyring access, process inspection, file-handle opens, and unused kernel interfaces while leaving the namespace and seccomp operations Gecko needs intact. Landlock ABI 6+ and unprivileged user namespaces are required; if confinement is unavailable or the bus proxy fails, the browser refuses to start or terminates immediately with no unconfined fallback.

The bus proxy identifies the browser to desktop portals as `org.nixos.ManagedFirefox` via the portal's Flatpak identity protocol without needing a Flatpak runtime installed. Each browser only sees its own document grants, and picking a file grants access strictly to that document rather than its parent directory. Writable browser directories must not be symlinks.

### Download directories

Daily Firefox downloads into `~/Downloads` and sees an empty read-only mount at `~/Downloads/identity`, whereas identity Firefox reads and writes the real `~/Downloads/identity`. Existing identity downloads stay put and can be opened from identity Firefox or your desktop file manager; if you need to open one in daily Firefox, select it through the desktop file chooser (grants belong only to the requesting browser identity). The portal browser maps `~/Downloads` to temporary storage that is wiped when its session ends.

### Existing windows and restarts

The normal launcher hands off new links to an already-running window via its exact profile-specific D-Bus name, while generation records and a host launch lock stay outside the browser mounts. Close all old windows before launching a rebuilt package; the managed profile carries over across the restart. `firefox --check` also verifies the private runtime directory, desktop sockets, and Landlock support without opening a window.

The daily transparent path runs under the desktop UID with system DNS and A/B/C rules, retaining access to other authorized local services. Wayland, audio, `Downloads`, the profile, and user-chosen documents are explicitly exposed—keep in mind that any other unconfined processes running under your desktop UID remain part of the trust base and could invoke the private binaries directly.

## Network policy and identities

| Entry | Available modes | Profile and network |
|---|---|---|
| `firefox` | **A** and healthy **B** by default | Persistent daily profile using native sockets on this host |
| `firefox-identity` | Healthy **B** only | Separate profile, cache, and downloads; only uBlock Origin is allowed |
| `firefox-portal URL` | Explicit **A** only | Temporary profile and a relay restricted to one literal IP on ports 80/443, for up to 15 minutes |

With `my.firefox.dailyRequireProxy=false`, daily Firefox uses direct access in **A** and subscription `Rule` routing in **B** (which can include `DIRECT` rules). Setting it to `true` forces the daily browser through the same strict inlet used by the identity browser (`my.firefox.requireProxy` is kept as an alias for the daily setting).

The strict inlet requires `my.proxy.strictBrowser=true`. Inside it, the browser has only loopback networking and talks to the host over `/run/clash-strict/proxy.sock`. The host relay accepts traffic only in healthy **B**, checks the connected core socket's UID, and closes streams as soon as readiness drops (polling published state every 100 ms on top of the supervisor's detection time), while `nftables` also blocks the inlet in **A** and **C**.

Inside the core, the fixed `lf-strict` inlet routes through `lf-remote` and rejects `DIRECT`, `COMPATIBLE`, and private targets. In `rules` mode this group keeps its own node selection; in runtime `proxy-only`, it is bound to the same fixed leaf as ordinary apps and DNS, changeable only via a root exit transaction. Neither GUI selections nor Global mode can relax those rules, and the browser has no direct path to host TCP, LAN, native DNS, or the internet. Already-queued or transmitted bytes cannot be recalled, and unconfined processes under the desktop UID remain trusted.

Both daily and identity profiles lock HTTPS-only mode as well as phishing, malware, and download protection, with certificate-warning bypass disabled (note that Safe Browsing can disclose query or download metadata, as [Mozilla explains](https://support.mozilla.org/en-US/kb/how-does-phishing-and-malware-protection-work)). To open a portal session, switch explicitly to **A** with `clash-off` first; the launcher checks the applied firewall policy so the chosen literal IP works even when public DNS is offline, binds the relay to that direct authorization, tears down connections when it expires, and exits the browser while wiping the temporary profile, cache, and downloads.

Separate profiles isolate cookies and account state, though they do not anonymize a logged-in account. WebRTC, HTTP/3, and browser DoH are all disabled, and proxy name resolution follows the shared [DNS and target policy](dns.md).

## ECH discovery and proxy compatibility

ECH encrypts the inner TLS ClientHello, including the origin SNI. In this setup, the browser gets its `ECHConfig` from an HTTPS DNS record (GREASE sends a synthetic ECH-shaped extension, while a real ECH handshake encrypts `ClientHelloInner` with the server's `ECHConfig`).

### Browser paths

| `my.firefox.networkPath` | Name discovery and transport | ECH behavior |
|---|---|---|
| `"transparent"`, host default | System DNS and native HTTPS-RR queries; **A** uses direct sockets, **B** uses the host TUN/firewall path | NSS can use the returned `ECHConfig` for a real ECH handshake with compatible origins |
| `"http-proxy"` | Strict local relay or compatibility Gost HTTP proxy; target resolution is delegated to the core | Pinned Firefox 160.0 excludes HTTPS `CONNECT` from HTTPS-RR discovery; GREASE stays enabled |

Firefox's [connection gate](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/protocol/http/nsHttpChannel.cpp) checks both the proxy DNS strategy and `UsingConnect()`, so changing DNS transport alone does not open it. This package keeps the test-only `force_use_https_rr` override disabled, and the transparent path uses the packaged browser without patching Gecko.

Daily Firefox defaults to `"transparent"` when `my.proxy.transparentApplications = true` and `dailyRequireProxy = false`, and `"http-proxy"` in compatibility setups. To enable the transparent path explicitly:

```nix
my.proxy.tunMode = true;
home-manager.users.lfour.my.firefox.dailyRequireProxy = false;
home-manager.users.lfour.my.firefox.networkPath = "transparent";
```

Rebuild the generation and fully restart Firefox afterward (the module rejects transparent mode when `tunMode=false`). The network path is baked in at package build time, while the launcher still checks network readiness—**C** blocks traffic in the kernel and refuses new launches, and **A** keeps literal HTTP portal access. `bootMode`, `proxyOnly`, fingerprinting protection, and `dnsEncryption` remain independent knobs.

### Where discovery happens

Here is how the normal encrypted public-DNS path flows (DNS recovery and private resolver exceptions follow the [DNS policy](dns.md)):

```text
Firefox native HTTPS query
  -> libc resolver -> systemd-resolved -> dnsmasq suffix filter
       A: Unbound -> authenticated DoT + local DNSSEC validation
       B: core suffix filter -> local validator when localDnssec=true
                               otherwise configured encrypted resolver
  <- HTTPS RR carrying ECHConfig

Firefox/NSS builds the encrypted ClientHello
  -> A: direct TCP
  -> B: managed TUN/core -> Rule DIRECT or selected proxy -> origin
```

On Unix, Firefox uses the [native resolver API](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/dns/PlatformDNSUnix.cpp) for type 65 records. Browser DoH stays disabled and locked—the existing resolver chain already carries HTTPS records as ordinary DNS messages without needing another local DoH endpoint.

Inside the core, address records still get fake-IP responses while HTTPS and SVCB records pass through the normal resolver. The shared filter runs first, returning local NXDOMAIN instead of an `ECHConfig` for blocked domains; record parameters (including the ECH public name) go back to the browser, and the core never consumes the browser's `ECHConfig` or terminates origin TLS. **A** validates DNSSEC locally, and **B** does too when `localDnssec=true`, while encrypted transport, unsigned zones, approved split DNS, and MagicDNS keep their [separate boundaries](dns.md#responsibilities).

NSS picks the config, builds `ClientHelloInner`/`Outer`, verifies the certificate, and handles authenticated retry configs. Keep in mind that GREASE and `ECHAccepted` are separate signals: a server doing ordinary TLS can see GREASE while still reporting that ECH was not accepted.

### Routing and failure behavior

Managed core sniffing is disabled in both `Rule` and uniform proxy modes. An ECH outer SNI is only a cover name and never replaces the requested target; domain routing uses explicit proxy targets and fake-IP mappings, while literal IP traffic follows IP rules (the root parser enforces this on cached GUI profiles too).

When no usable HTTPS/ECH record is available, ordinary certificate-verified TLS still works, and ECH is used wherever a site publishes a usable config. Setting `network.dns.echconfig.fallback_to_origin_when_all_failed=false` disables the preference-controlled origin fallback, but does not block NSS's separate authenticated retry without ECH: if a trusted server rejects ECH without usable retry keys, NSS can open a fresh ordinary TLS connection with the origin SNI. Authenticated key retries are allowed, whereas an untrusted rejection or invalid certificate never authorizes a retry—so missing/stripped records, DNS failures, and authenticated rejections behave differently. Note that `dnsEncryption="strict"` governs DNS transport and does not require every site to support ECH.

Remember that an explicit HTTP proxy always sees `CONNECT` targets (even if another client speaks ECH through it), and on the transparent path the local core sees DNS/fake-IP mappings and routing targets. ECH does not hide destination IPs, traffic timing, accounts, or content visible to the origin; see [RFC 9849](https://www.rfc-editor.org/rfc/rfc9849.html) for the protocol's acceptance and retry rules.

### Maintaining the integration

Firefox, NSS, and resolver updates can alter ECH discovery and retry behavior. See the [maintenance guide](maintenance.md#dns-and-browser-integration) for the relevant browser and core checks.

## Privacy settings

`strictPrivacy` is enabled in the module, and AutoConfig `lockPref` locks preferences beyond what enterprise policy supports while resisting conflicting `user.js` entries across every profile opened with the package.

| Layer | Behavior and boundary |
|---|---|
| Fingerprinting | RFP and letterboxing reduce Canvas, timezone, hardware, and window-size signals; a customized desktop still differs from Tor Browser |
| Tracking and state | Strict protection, FPP, and Total Cookie Protection reduce script and cross-site state correlation |
| URLs and profiling | Query stripping, bounce cleanup, cross-origin referrer restrictions, and GPC; suggestions, speculation, telemetry, studies, sponsored content, Sync, and built-in IP Protection disabled |
| APIs and fonts | WebRTC, WebGPU, geolocation, battery, and sensors disabled; strict privacy also disables WebGL and restricts visibility to fixed DejaVu/Noto/CJK/emoji fonts |
| ECH / TLS | ECH and GREASE enabled; the daily transparent path supports ECH discovery through native HTTPS records |
| Exit address | Shared A/B/C and routing policy; default **B** `Rule` routing selects `DIRECT` or a proxy node |

Strict extension policy blocks undeclared extensions: both daily and identity Firefox force-install uBlock Origin, KISS Translator is removable in daily Firefox, and the portal browser blocks all extensions. Signed packages and updates come from Mozilla Add-ons over the active network path when online, though package policy and Gecko protections do not wait on extension downloads.

Gecko's content-process sandbox, the outer confinement, and privacy preferences each protect a different layer—none of them changes the routing or resolver trust boundaries above. Package integration and upgrade checks are in [Patch maintenance](maintenance.md), and resolver behavior is in [DNS](dns.md).

### Windows User-Agent for selected sites

If a site expects daily Firefox to report itself as Firefox on Windows, add it in Home Manager, apply the config, and restart the browser:

```nix
my.firefox.windowsUserAgentDomains = [ "example.org" ];
```

List each exact hostname (including any subdomains you need). Firefox will then report Windows in the HTTP `User-Agent` header and in JavaScript's `navigator.userAgent`, `navigator.platform`, `navigator.oscpu`, and `navigator.appVersion`, with the browser version tracking the packaged Firefox release.

The list is empty by default. Each listed hostname gets a Resist Fingerprinting (RFP) exception, which also relaxes RFP protections for signals like canvas and timezone, and sites can still infer the OS from other clues. Set the list back to `[ ]` and re-apply to remove the exceptions.
