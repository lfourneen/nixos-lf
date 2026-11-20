# Managed Firefox

English | [简体中文](CN/browser_CN.md) | [Documentation](README.md)

## Package and launch

Firefox is a native Nix package.
[firefox.nix](../home/programs/firefox/firefox.nix) binds the binary, enterprise policies, AutoConfig and fixed fonts into one immutable store closure.
Home Manager owns the separate `~/.config/mozilla/firefox/lfour` profile.
Updates follow the locked Nixpkgs input and a rebuilt generation, with Firefox's binary updater disabled.
Existing Flatpak profiles are not imported or deleted.

The launcher runs [guard.py](../home/programs/firefox/guard.py) before opening the managed profile.
In B, it requires matching, fresh mode, DNS, relay and verification state, then checks listener identity and the live controller policy.
Root services validate private configuration snapshots and publish the result for the browser.
With runtime routing, the launcher selects the fast check for the applied `rules` or `proxy-only` policy. It uses the desktop controller socket.

C, unknown state and `unenforced` refuse startup.
The final state read must agree on the policy generation and the routing and exit epochs. Opening a link in an existing instance uses the same checks.
Once Firefox is running, the relay and firewall enforce subsequent changes.

The launcher checks fonts under strict privacy and starts the browser in the outer sandbox described below.
Host-side instance records use process start times and package identity to reject reuse of an older package.
Namespace PIDs in stale profile locks do not identify desktop processes.
Desktop/MIME entries use it.
Former Zen/Chrome entry IDs redirect to Firefox.
Policy changes require a full browser restart.
`firefox --check` and `firefox-identity --check` inspect prerequisites without creating a profile or opening a window.

## Outer sandbox

[sandbox.py](../home/programs/firefox/sandbox.py) retains the desktop UID.
Strict browsers also get an isolated network namespace, while bubblewrap creates separate mount, user, PID, IPC and UTS namespaces.
The browser sees a synthetic home and a private `/tmp` and `/dev`.
Capabilities are dropped, inherited environment variables are replaced by an allow-list, and Gecko's content-process sandbox remains enabled.
The native Firefox binary is reused.
The Nix wrapper and small [scope helper](../home/programs/firefox/scope.c) supply the outer policy without a Gecko source patch.

| Resource | Browser access |
|---|---|
| Persistent files | Read/write managed profile, `~/.cache/firefox-managed` and `~/Downloads`. Other personal files require a file-chooser grant |
| Runtime and trust | Read-only Nix store, selected resolver/CA files and graphics driver. No host root filesystem or general home/runtime-directory mount |
| Display and audio | Named Wayland socket, optional Pulse socket and DRM render nodes. Configured NVIDIA access also exposes matching primary nodes and rendering interfaces. No X11 socket, input devices, camera or NVIDIA modeset interface |
| Session bus | Filtered proxy: file chooser, read-only settings, request/session close, scheme-support query and remoting for this exact profile |
| Other host interfaces | No system bus, SSH/GPG agents, container or core-controller sockets. Landlock prevents connecting to host abstract Unix sockets and signalling outside the scope |

### Certificates and graphics

System certificate trust uses the NixOS CA bundle at `/etc/ssl/certs/ca-certificates.crt`, including declared system CAs.
The launcher resolves its symlink and mounts the actual file read-only at that path: NSS/p11-kit can read it without exposing `/etc/static` or the full host certificate directory.
A missing bundle refuses launch.
Certificate verification remains enabled.

`my.firefox.nvidiaAcceleration` follows the declared NVIDIA driver by default.
It exposes the GPU nodes and read-only EGL configuration needed for rendering, while hiding unrelated devices and modesetting interfaces.
Set it to `false` for render-node-only access.
Gecko confinement remains enabled, and the locked privacy policy still disables WebGL.

### System interfaces and file access

The syscall filter also blocks keyring access, process inspection, file-handle opens and selected unused kernel interfaces.
It leaves the namespace and seccomp operations needed by Gecko available.
Landlock ABI 6 or newer and unprivileged user namespaces are required.
Missing confinement or a failed bus proxy refuses or terminates the browser.
There is no unconfined fallback.

The bus proxy identifies the browser to desktop portals as `org.nixos.ManagedFirefox`, using the portal's Flatpak identity protocol without installing a Flatpak runtime.
The browser sees only its own document grants.
Selecting a file grants access to that document, rather than its whole parent directory.
Writable browser directories must not be symbolic links.

### Existing windows and restarts

The normal launcher can open another link in the running browser through its exact profile-specific D-Bus name.
Generation records and a host launch lock stay outside the browser mounts.
Close all old windows before opening a rebuilt package.
The managed profile persists across that restart.
`firefox --check` also checks the private runtime directory, desktop sockets and Landlock support without opening a window.

## Network policy and identities

| Entry | Available modes | Profile and network |
|---|---|---|
| `firefox` | A and healthy B by default | Persistent daily profile using native sockets on this host |
| `firefox-identity` | Healthy B only | Separate profile, cache and downloads. Only uBlock Origin is allowed |
| `firefox-portal URL` | Explicit A only | Temporary profile and a relay restricted to one literal IP on ports 80/443, for up to 15 minutes |

`my.firefox.dailyRequireProxy=false` lets daily Firefox use direct access in A and subscription Rule routing in B.
B can include DIRECT rules.
Setting it to `true` selects the same strict inlet used by the identity browser.
The old `my.firefox.requireProxy` option is an alias for the daily setting.

The strict inlet requires `my.proxy.strictBrowser=true`.
Its browser runs with only loopback networking and reaches the host through `/run/clash-strict/proxy.sock`.
The host relay accepts traffic only in healthy B, verifies the connected core socket's UID, and closes streams when readiness is lost.
It polls published state every 100 ms, in addition to the supervisor's detection time.
A/C also block the inlet through nftables.

The core's fixed `lf-strict` inlet uses `lf-remote` and refuses DIRECT, COMPATIBLE and private targets.
In rules this group has its own node choice. Runtime proxy-only binds it to the same fixed leaf as ordinary applications and DNS; only a root exit transaction can change that leaf.
GUI selections and Global mode cannot relax those rules.
The browser cannot open host TCP, LAN, native DNS or direct internet connections.
Already queued or transmitted bytes cannot be recalled.
Other unconfined processes under the desktop UID remain trusted.

Daily and identity profiles lock HTTPS-only and phishing, malware and download protection.
Certificate-warning bypass is disabled.
Safe Browsing can disclose query or download metadata, as described by [Mozilla](https://support.mozilla.org/en-US/kb/how-does-phishing-and-malware-protection-work).
Use `clash-off` to explicitly select A before opening a portal session. The launcher checks the applied firewall policy, so the selected literal address is available while public DNS is offline.
The relay binds to that direct authorization and closes existing connections when it ends. The launcher then exits the browser and removes the temporary profile, cache and downloads.

Separate profiles keep cookies and account state apart.
They do not make a logged-in account anonymous.
WebRTC, HTTP/3 and browser DoH are disabled.
Proxy name resolution follows the shared [DNS and target policy](dns.md).

## ECH discovery and proxy compatibility

ECH encrypts the inner TLS ClientHello, including the origin SNI.
In this configuration, the browser obtains its ECHConfig from an HTTPS DNS record.
GREASE adds a synthetic ECH-shaped extension.
A real ECH handshake uses the server's ECHConfig to encrypt ClientHelloInner.

### Browser paths

| `my.firefox.networkPath` | Name discovery and transport | ECH behavior |
|---|---|---|
| `"transparent"`, host default | System DNS and native HTTPS-RR queries. A uses direct sockets, B uses the host TUN/firewall path | NSS can use the returned ECHConfig and perform a real ECH handshake with a compatible origin |
| `"http-proxy"` | Strict local relay or compatibility Gost HTTP proxy. Target resolution is delegated to the core | The pinned Firefox 160.0 excludes HTTPS CONNECT from HTTPS-RR discovery. GREASE remains enabled |

Firefox's [connection gate](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/protocol/http/nsHttpChannel.cpp) checks both the proxy DNS strategy and `UsingConnect()`.
Changing DNS transport alone does not change this gate.
This package keeps the test-only `force_use_https_rr` override disabled.
The transparent option uses the packaged browser without a Gecko source patch.

Daily Firefox selects `"transparent"` by default when `my.proxy.transparentApplications = true` and `dailyRequireProxy = false`.
The compatibility default is `"http-proxy"`. To select the transparent browser path independently:

```nix
my.proxy.tunMode = true;
home-manager.users.lfour.my.firefox.dailyRequireProxy = false;
home-manager.users.lfour.my.firefox.networkPath = "transparent";
```

Rebuild the generation and fully restart Firefox.
The module rejects transparent operation with `tunMode=false`.
The network path is selected when building the package.
The same launcher still checks network readiness.
C blocks traffic in the kernel and refuses new launches.
A keeps literal HTTP portal access.
`bootMode`, `proxyOnly`, browser fingerprinting protection and `dnsEncryption` remain independent choices.

### Where discovery happens

The normal encrypted public-DNS path is shown below.
DNS recovery and private resolver exceptions follow the [DNS policy](dns.md).

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

The Unix browser uses the [native resolver API](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/dns/PlatformDNSUnix.cpp) for type 65.
Browser DoH remains disabled and locked.
No additional local DoH endpoint is needed: the existing resolver chain transports HTTPS records as DNS messages.

The core retains fake-IP address behavior while forwarding HTTPS and SVCB records through its normal resolver.
The shared filter runs first, so a blocked domain receives local NXDOMAIN instead of an ECHConfig.
Record parameters, including the ECH public name, are carried back to the browser.
The core does not consume the browser's ECHConfig or terminate its origin TLS session.
A validates DNSSEC locally, and B does so when `localDnssec=true`.
Encrypted transport, unsigned zones, approved split DNS and MagicDNS retain their [separate boundaries](dns.md#responsibilities).

NSS selects the config, constructs ClientHelloInner/Outer, verifies the certificate and handles authenticated retry configs.
GREASE and `ECHAccepted` are different observations: a service accepting ordinary TLS can see GREASE while reporting that ECH was not accepted.

### Routing and failure behavior

Managed core sniffing is disabled in both Rule and uniform proxy modes.
An ECH outer SNI is a cover name and cannot replace the requested target.
Explicit proxy targets and fake-IP mappings supply names for domain routing.
Literal IP traffic follows IP rules.
The root parser applies this policy to cached GUI profiles too.

When no usable HTTPS/ECH record exists, ordinary certificate-verified TLS remains available.
ECH is used for sites with a usable configuration.
`network.dns.echconfig.fallback_to_origin_when_all_failed=false` disables the origin fallback controlled by that preference, but does not prohibit NSS's separate authenticated retry without ECH.
A trusted server rejecting ECH with no usable retry keys can cause a new ordinary TLS connection carrying the origin SNI.

Authenticated key retries remain allowed.
An untrusted rejection or invalid certificate does not authorize this retry.
Missing/stripped records, DNS failure and authenticated rejection therefore have different outcomes.
`dnsEncryption="strict"` governs DNS transport and does not require every site to support ECH.

An explicit HTTP proxy can see CONNECT targets, including when another client supports ECH through that proxy.
The native path's local core can see DNS/fake-IP mappings and routing targets.
ECH does not hide destination IPs, traffic timing, accounts or the content visible to the origin.
See [RFC 9849](https://www.rfc-editor.org/rfc/rfc9849.html) for the protocol's acceptance and retry rules.

### Maintaining the integration

Firefox, NSS and resolver updates can change ECH discovery and retry behavior.
The [maintenance guide](maintenance.md#dns-and-browser-integration) lists the relevant browser and core checks.

## Privacy settings

`strictPrivacy` is enabled in the module.
AutoConfig `lockPref` covers preferences beyond enterprise policy lock support and resists conflicting `user.js` values for every profile opened with the package.

| Layer | Behavior and boundary |
|---|---|
| Fingerprinting | RFP and letterboxing reduce Canvas, timezone, hardware and window-size signals. Customization still differs from Tor Browser |
| Tracking and state | Strict protection, FPP and Total Cookie Protection reduce scripts and cross-site state correlation |
| URLs and profiling | Query stripping, bounce cleanup, cross-origin referrer restrictions and GPC. Suggestions, speculation, telemetry, studies, sponsored content, Sync and built-in IP Protection disabled |
| APIs and fonts | WebRTC, WebGPU, geolocation, battery and sensors disabled. Strict privacy also disables WebGL and restricts visibility to fixed DejaVu/Noto/CJK/emoji fonts |
| ECH / TLS | ECH and GREASE enabled. The daily transparent path supports ECH discovery through native HTTPS records |
| Exit address | Shared A/B/C and routing policy. Default B Rule routing selects DIRECT or a proxy node |

Strict extension policy blocks undeclared extensions. Daily and identity Firefox force-install uBlock Origin.
KISS Translator is removable in daily Firefox. The portal browser blocks all extensions.
Signed packages/updates come from Mozilla Add-ons through the selected network path and require connectivity.
Package policy and Gecko protections do not depend on extension download completing.

Gecko's content-process sandbox and the outer policy protect different layers.
The outer policy constrains the native parent and its descendants.
Privacy preferences constrain browser behavior.
Neither changes the routing or resolver trust boundaries above.
Package integration and upgrade checks are covered in [Patch maintenance](maintenance.md).
Resolver behavior is covered in [DNS](dns.md).

### Windows User-Agent for selected sites

Set the following in Home Manager if a site needs daily Firefox to identify itself as Firefox on Windows. Apply the configuration and restart the browser.

```nix
my.firefox.windowsUserAgentDomains = [ "example.org" ];
```

List each exact hostname, including any subdomains you need. Firefox reports Windows in the HTTP `User-Agent` header and JavaScript's `navigator.userAgent`, `navigator.platform`, `navigator.oscpu` and `navigator.appVersion`. The browser version follows the packaged Firefox version.

The list is empty by default. Each listed hostname uses a Resist Fingerprinting (RFP) exception, which also relaxes RFP protections for signals such as canvas and time zone. Sites can still infer the operating system from other signals. Set the list back to `[ ]` and apply the configuration to remove the exceptions.
