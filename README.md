<p align="center">
  <a href="https://nixos.org">
    <img src="assets/nixos-logomark-default-gradient-minimal.svg" width="140" alt="NixOS logo">
  </a>
</p>

<h1 align="center">nixos-lf</h1>

<p align="center">Personal NixOS workstation configuration</p>

<p align="center">English | <a href="README_CN.md">简体中文</a></p>

This is my personal NixOS workstation configuration, managed with Nix flakes and Home Manager. It sets up a Niri desktop, daily apps, development tools, and encrypted storage, with a network stack built around an nftables firewall, system-wide transparent proxying via Mihomo (Clash), and encrypted DNS with local DNSSEC validation.

The system builds from `nixosConfigurations.lfour` for `x86_64-linux`, tracking NixOS 26.05 with selected packages pulled from Unstable.

## Network security and architecture

The host firewall (`nftables`) governs IPv4 and IPv6 ingress, egress, and guest forwarding, with a systemd coordinator applying mode transitions atomically. Whenever proxy mode is requested, the system enters **C** (blocked) first and only opens **B** (proxy) after listener, routing, DNS, and TUN health checks all pass. A 60-second kernel lease ties **B** to fresh health checks—if the lease expires, `nftables` cuts off ordinary application traffic, including already-established flows. Details are in [modes and enforcement](docs/network.md#modes-and-enforcement).

| Mode | Ordinary application traffic | Public DNS |
|---|---|---|
| **A** · Direct | Native sockets go straight out over Ethernet or Wi-Fi | Unbound queries over DoT and validates DNSSEC locally |
| **B** · Proxy | TCP/UDP enters Mihomo via TUN; TCP fallback redirects through nftables and Gost | Mihomo validates DNSSEC locally and queries DoH upstream, returning fake IPs by default (or validated real addresses when using the WireGuard exit) |
| **C** · Blocked | Ordinary external traffic is blocked, leaving only scoped link maintenance and recovery paths open | Local resolution and eligible cached answers remain available |

- **Transparent proxy and exit policy.** Both paths into Mihomo cross a private `veth` link into its own network namespace, and the host firewall checks the core's traffic across that boundary. In **B**, `rules` mode follows the subscription's direct, proxy, and reject rules, whereas `proxy-only` pins supported traffic to a single approved exit and blocks independent application egress. An optional WireGuard peer can carry the fixed SOCKS5 relay's traffic along with native IPv4/IPv6 ping. See [traffic paths](docs/network.md#traffic-paths) and [routing policy](docs/proxy.md#routing-policy).
- **Subscription trust and GUI controls.** A root-owned broker registers subscription sources and authorizes audited snapshots, while an unprivileged worker fetches and renders candidates. System services own the routing policy and reserve the DNS and proxy ports; Clash Verge talks over a restricted Unix socket strictly for monitoring and choosing permitted nodes. See [controlled sources and service identities](docs/proxy.md).
- **DNS routing and filtering.** System queries flow through `systemd-resolved → dnsmasq → Unbound or Mihomo`. `nftables` redirects ordinary outbound port 53 traffic, and `dnsmasq` shares a domain blocklist with Mihomo. Approved Wi-Fi split DNS and Tailscale MagicDNS get scoped paths in compatible modes. See [DNS policy](docs/dns.md).
- **Uplinks and local services.** `dhcpcd` handles Ethernet and NetworkManager handles Wi-Fi, with support for per-profile MAC, DHCP, and IPv6 identities as well as ARP pinning for the IPv4 gateway on either uplink. Mode-specific firewall rules allow link maintenance, Chrony with Network Time Security (NTS), and Tailscale access to declared services like Sunshine. See [link identity](docs/identity.md) and [direct egress exceptions](docs/egress.md).
- **Guests and browser isolation.** Routed guests use bridge- and subnet-scoped rules with opt-in IPv4 TPROXY in **B**, while restricted container backends that open host sockets are isolated by dedicated UIDs. Daily Firefox follows the host network policy; the identity profile runs in its own network namespace over a B-only proxy relay, and captive-portal logins use a disposable browser in **A**. See [guest networking](docs/network.md#guests-and-containers) and [Firefox network policy](docs/browser.md#network-policy-and-identities).

Both the [network modules](system/config/network/) and [NixOS VM tests](tests/checks.nix) cover mode transitions, DNS routing, guest isolation, and WireGuard egress. For configuration and recovery instructions, start from the [network documentation](docs/README.md).

## Desktop and system

- **Wayland desktop.** Niri is the default session, paired with Noctalia for the bar, launcher, notifications, and wallpaper-driven theming. GNOME is also available in GDM, and Fcitx5 with Rime handles Chinese input.
- **Storage and persistence.** Disko lays out a LUKS2-encrypted Btrfs setup where the root subvolume is wiped and recreated on every boot, while Impermanence keeps selected system and application state under `/persist`.
- **Development environment.** Nushell, Neovim (with LSPs), VS Code, and Zed are configured alongside rootless Docker, virtualization tools, and FHS environments for software that expects a traditional Linux filesystem layout.
- **Firefox.** Packaged natively with locked privacy settings, an outer filesystem sandbox, a separate identity profile, and a temporary portal browser. Startup checks the current network state before launching.
- **Hermes Agent.** The agent, gateway, dashboard, MCP integrations, and skills are all declared in Nix. Agent state lives in `/var/lib/hermes`, using a separate account and Docker backend for container workloads.
- **Hardware and remote access.** AMD/NVIDIA graphics, audio, Bluetooth, USBGuard, and Sunshine streaming over Tailscale are tailored to my hardware. See the [USBGuard guide](scripts/README.md#usbguard-policy) for initial setup and device authorization.

The [source layout](docs/layout.md) maps each of these features to its modules.

## Using this configuration

Because this setup is tailored to a specific machine, you will want to review the user accounts, hardware IDs, disk layout, persistence paths, and encrypted secrets before adapting it. Pay special attention to Disko and the boot-time root reset if you are working on an existing system—for a fresh install, there is a separate [Disko installation example](https://github.com/lfourneen/nixos-disko-lf).

Clone the repository:

```sh
git clone https://github.com/lfourneen/nixos-lf.git
cd nixos-lf
```

These are the main files to adapt first:

| File | What to adapt |
|---|---|
| [flake.nix](flake.nix) | System output, user integration, and input versions |
| [system/config/user.nix](system/config/user.nix) | Accounts and groups |
| [system/config/machine.nix](system/config/machine.nix) | Network interfaces, addresses, ports, and service identities |
| [system/hardware/](system/hardware) | Disk layout, boot, CPU, and GPU configuration |
| [system/hardware/partition.nix](system/hardware/partition.nix) | Persistent directories and files |
| [system/programs/sops.nix](system/programs/sops.nix) | Secret declarations and Age key location |
| [home/](home) | Desktop, shell, editor, and application preferences |

Since the host enforces strict encrypted DNS and requires registered proxy sources, complete [source registration](docs/operations.md#source-registration) before expecting proxy mode to work. Once proxy access is requested, an unavailable core blocks ordinary application traffic until it recovers or you explicitly switch back to direct access.

Build the adapted system first:

```sh
nix build --no-link .#nixosConfigurations.lfour.config.system.build.toplevel
```

Then activate it when ready:

```sh
sudo nixos-rebuild switch --flake .#lfour
```

If you rename the flake output, replace `lfour` in both commands accordingly, and point future rebuilds at your own checkout.

Release branches and the shared `stateVersion` live in [flake.nix](flake.nix). When upgrading NixOS, bump the Nixpkgs and Home Manager input branches together, and leave `stateVersion` alone unless you have reviewed the breaking changes and migrated the affected application data.

## Daily networking

| Command | Purpose |
|---|---|
| `clash-on` | Request proxy access and start the managed core |
| `clash-off` | Switch to **A** and confirm direct firewall and routing policy |
| `clash-rules` | Select subscription rules and request healthy **B** |
| `clash-proxy-only` | Select strict proxy routing and request healthy **B** |
| `clash-egress [ID]` | List accepted exit candidates or select one via a protected switch |
| `sudo proxy-control recover [--cached]` | Fetch a fresh candidate or apply a previously downloaded one while **C** blocks application traffic |
| `proxy-status` | Show mode, DNS, and readiness |
| `clash-diff` | Inspect source changes without changing network mode |
| `firefox-identity` | Open the separate browser profile through the strict proxy inlet |
| `firefox-portal http://192.168.1.1/` | Open a temporary portal session in direct mode |

In short: **A** is direct access, **B** is healthy proxy access, and **C** blocks application traffic whenever proxy access is unavailable. Closing the GUI leaves the managed core running; after `clash-off` switches back to **A**, you can check direct DNS readiness separately with `proxy-status`.

Clash Verge is kept as the upstream GUI for node selection and monitoring, while the system silently drops any System Proxy, TUN, or core-mode requests it makes. Use the system commands above to toggle between `rules` and `proxy-only`—both keep Mihomo in `mode: rule` under the hood. When you run `clash-on`, controlled subscriptions prefer their accepted snapshot; routine startup updates wait until **B** is healthy, though an unhealthy restored snapshot can trigger one bounded recovery attempt in **C**.

Clicking the refresh button in Clash Verge submits a registered subscription update to the system. Scheduled refreshes follow the profile's interval (30 minutes by default) and continue running in the background even with the GUI closed if automatic updates are enabled. If an update leaves forwarding unchanged and the running snapshot is still authorized and ready, existing connections stay open; if forwarding actually changes, the system enters **C** before restarting the core. Use `proxy-status` to check download and apply results (see [refresh status](docs/operations.md#refresh-status)), and refer to [operations and recovery](docs/operations.md) for source management, diagnostics, and offline recovery.

## Hermes and secrets

Hermes configuration and skills live in [system/programs/hermes/](system/programs/hermes). You can launch the CLI with `hermes` in Nushell, or `sudo -u hermes -i hermes` from any other shell. Its container endpoint and data handling are documented in [Hermes containers](docs/operations.md#hermes-containers).

Secrets are managed with `sops-nix`. By default, system services decrypt using the root-only Age identity at `/persist/secrets/age/system.key`, so make sure that key is in place before the first rebuild. The [scripts guide](scripts/README.md#system-age-keypy) walks through adopting an existing `age.txt` or `keys.txt`, generating a new identity, adding its recipient to `.sops.yaml`, and re-encrypting existing files. Keep the private key out of Git and back it up offline. (To fall back to `~/.config/sops/age/keys.txt`, set `my.secrets.systemAgeKey.enable = false`.)

Edit system secrets as root with the system identity explicitly selected:

```sh
sudo SOPS_AGE_KEY_FILE=/persist/secrets/age/system.key \
  sops edit system/secrets/secrets.yaml  # API and messaging credentials
sudo SOPS_AGE_KEY_FILE=/persist/secrets/age/system.key \
  sops edit system/secrets/soul.yaml     # Hermes persona
sudo SOPS_AGE_KEY_FILE=/persist/secrets/age/system.key \
  sops edit system/secrets/proxy.yaml    # Proxy source catalog
```

Saving as root changes file ownership, so restore it with `sudo chown "$USER": ...` before committing, then rebuild to apply the changes. Secret names and service ownership are declared in [sops.nix](system/programs/sops.nix).

## Repository guide

| Directory | Contents |
|---|---|
| [system/](system) | System services, hardware, network policy, and packages |
| [home/](home) | Home Manager modules and user applications |
| [overlays/](overlays) | Package overrides |
| [local-data/](local-data) | Versioned inputs such as the DNS blocklist snapshot |
| [maintenance/](maintenance) | Repository-wide update inventory and review commands |
| [scripts/](scripts) | Configuration copy tools and blocklist refresh utility |
| [tests/](tests) | Package, policy, and isolated VM checks |
| [assets/](assets) | README logo and its attribution |
| [docs/](docs/README.md) | Architecture, configuration, operations, and maintenance |

Run `python3 scripts/check-maintenance.py` to validate the update inventory. When modifying the network stack or bumping dependencies, consult the [maintenance guide](docs/maintenance.md) to pick the relevant checks and review local patches.

## License

This project is licensed under the MIT License—see [LICENSE](LICENSE) for details.
