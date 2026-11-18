# nixos-lf · Personal NixOS workstation configuration

English | [简体中文](README_CN.md)

A personal NixOS system configuration managed with Nix flakes and Home Manager. It includes a Niri desktop, applications, development tools and encrypted storage.
The workstation network integrates an nftables firewall, system-wide transparent proxying with Mihomo (Clash), and encrypted DNS with DNSSEC validation.
The system output is `nixosConfigurations.lfour` for `x86_64-linux`, based on NixOS 26.05 with selected packages from Unstable.

## Network security and architecture

The host's nftables firewall controls IPv4 and IPv6 ingress, egress and guest forwarding. A systemd coordinator applies mode changes atomically. Entering proxy mode passes through C and opens B after listener, route, DNS and TUN health checks pass. A 60-second kernel lease ties B to fresh health checks. When the lease expires, nftables blocks ordinary application traffic, including existing flows. See [modes and enforcement](docs/network.md#modes-and-enforcement).

| Mode | Ordinary application traffic | Public DNS |
|---|---|---|
| **A** · Direct | Native sockets use Ethernet or Wi-Fi | Unbound uses DoT and validates DNSSEC locally |
| **B** · Proxy | TCP/UDP enters Mihomo through TUN. The TCP fallback uses nftables redirection via Gost | Mihomo uses local DNSSEC validation and DNS over HTTPS (DoH). Answers are normally fake IPs, or validated real addresses with the WireGuard exit |
| **C** · Blocked | Ordinary external traffic is blocked, with scoped link maintenance and recovery traffic allowed | Local and eligible cached answers remain available |

- **Transparent proxy and exit policy.** Both paths into Mihomo cross a private veth link into its network namespace. The host firewall checks the core's transport across that boundary. In B, `rules` applies the subscription's direct, proxy and reject rules. `proxy-only` pins supported traffic to one approved exit and blocks independent application egress. An optional WireGuard peer carries the fixed SOCKS5 relay's traffic and native IPv4/IPv6 ping. See [traffic paths](docs/network.md#traffic-paths) and [routing policy](docs/proxy.md#routing-policy).
- **Subscription trust and GUI controls.** A root-owned broker registers subscription sources and authorizes audited snapshots. An unprivileged worker fetches and renders candidates. System services own the routing policy and reserve the DNS and proxy ports. Clash Verge uses a restricted Unix socket for monitoring and permitted node selection. See [controlled sources and service identities](docs/proxy.md).
- **DNS routing and filtering.** System queries follow `systemd-resolved → dnsmasq → Unbound or Mihomo`. nftables redirects ordinary outbound port 53 queries, and dnsmasq and Mihomo share a domain blocklist. Approved Wi-Fi split DNS and Tailscale MagicDNS have scoped paths in compatible modes. See [DNS policy](docs/dns.md).
- **Uplinks and local services.** dhcpcd manages Ethernet and NetworkManager manages Wi-Fi. Wi-Fi supports per-profile MAC, DHCP and IPv6 identities, and either uplink can pin its IPv4 gateway with ARP. Mode-specific firewall allowances cover link maintenance, Chrony with Network Time Security (NTS), and Tailscale access to declared services such as Sunshine. See [link identity](docs/identity.md) and [direct egress exceptions](docs/egress.md).
- **Guests and browser isolation.** Routed guests use bridge- and subnet-scoped rules, with opt-in IPv4 TPROXY in B. Restricted container backends that open host sockets are identified by dedicated UIDs. Daily Firefox follows host policy, the identity profile uses a separate network namespace and a B-only proxy relay, and portal login uses a temporary browser in A. See [guest networking](docs/network.md#guests-and-containers) and [Firefox network policy](docs/browser.md#network-policy-and-identities).

The [network modules](system/config/network/) and [NixOS VM test definitions](tests/checks.nix) cover mode switches, DNS routing, guest isolation and WireGuard egress. See the [network documentation](docs/README.md) for configuration and recovery.

## Desktop and system

- **Wayland desktop.**
  Niri is the default session, with Noctalia for the bar, launcher, notifications and wallpaper-based theming.
  GNOME is also available through GDM.
  Fcitx5 with Rime provides Chinese input.

- **Storage and persistence.**
  Disko declares a LUKS2-encrypted Btrfs layout.
  The root subvolume is recreated at boot, while Impermanence keeps selected system and application state under `/persist`.

- **Development environment.**
  Nushell, Neovim with language servers, VS Code and Zed sit alongside rootless Docker, virtualisation tools and FHS environments for software that needs a conventional Linux layout.

- **Firefox.**
  A native package includes locked privacy settings, a filesystem sandbox, a separate identity profile and a temporary portal browser.
  Browser startup follows the network state.

- **Hermes Agent.**
  The agent, gateway, dashboard, MCP integrations and skills are declared in Nix.
  Agent data lives in `/var/lib/hermes`, with a separate account and Docker backend for container workloads.

- **Hardware and remote access.**
  AMD/NVIDIA graphics, audio, Bluetooth, USBGuard and Sunshine over Tailscale are configured for the author's machine.
  The [USBGuard guide](scripts/README.md#usbguard-policy) covers first-time setup and device authorization.

The [source layout](docs/layout.md) identifies the modules behind these features.

## Using this configuration

This is a machine-specific configuration.
Before adapting it, review the user account, hardware identifiers, disk layout, persistence paths and encrypted secrets.
Disko and the boot-time root reset need particular care on an existing installation.
A separate [Disko installation example](https://github.com/lfourneen/nixos-disko-lf) covers a clean install.

Clone the repository:

```sh
git clone https://github.com/lfourneen/nixos-lf.git
cd nixos-lf
```

Start with these files:

| File | What to adapt |
|---|---|
| [flake.nix](flake.nix) | System output, user integration and input versions |
| [system/config/user.nix](system/config/user.nix) | Accounts and groups |
| [system/config/machine.nix](system/config/machine.nix) | Network interfaces, addresses, ports and service identities |
| [system/hardware/](system/hardware) | Disk layout, boot, CPU and GPU configuration |
| [system/hardware/partition.nix](system/hardware/partition.nix) | Persistent directories and files |
| [system/programs/sops.nix](system/programs/sops.nix) | Secret declarations and Age key location |
| [home/](home) | Desktop, shell, editor and application preferences |

The host uses strict encrypted DNS and registered proxy sources.
Complete [source registration](docs/operations.md#source-registration) before expecting proxy mode to work.
Once proxy access is requested, an unavailable core blocks application traffic until it recovers or you explicitly select direct access.

Build the adapted system first:

```sh
nix build --no-link .#nixosConfigurations.lfour.config.system.build.toplevel
```

Then activate it when ready:

```sh
sudo nixos-rebuild switch --flake .#lfour
```

Replace `lfour` in these commands if you rename the flake output.
Later rebuilds should use the checkout you maintain.

Release branches and the shared `stateVersion` are configured in [flake.nix](flake.nix).
When upgrading NixOS, update the Nixpkgs and Home Manager input branches together.
Keep `stateVersion` unchanged unless you have reviewed and migrated the affected application data.

## Daily networking

| Command | Purpose |
|---|---|
| `clash-on` | Request proxy access and start the managed core |
| `clash-off` | Select direct access |
| `clash-rules` | Select subscription rules and request healthy B |
| `clash-proxy-only` | Select strict proxy routing and request healthy B |
| `clash-egress [ID]` | List accepted exit candidates or select one through a protected switch |
| `proxy-status` | Show mode, DNS and readiness |
| `clash-diff` | Inspect source changes without changing network mode |
| `firefox-identity` | Open the separate browser profile through the strict proxy inlet |
| `firefox-portal http://192.168.1.1/` | Open a temporary portal session in direct mode |

A is direct access, B is healthy proxy access, and C blocks application traffic while proxy access is unavailable.
Closing the GUI leaves the managed core active. Use `clash-off` to select A.
Clash Verge is the upstream GUI for node selection and monitoring. The system discards its System Proxy, TUN and core-mode requests.
Use the system commands to select `rules` or `proxy-only`. Both keep Mihomo in `mode: rule`.
With `clash-on`, controlled subscriptions prefer their accepted snapshot. Startup fetching waits until B is healthy.
Registered subscriptions also refresh every 30 minutes, with routine node rotation approved automatically.
Successful updates stay silent; failures appear in desktop notifications and `proxy-status`.
See [operations and recovery](docs/operations.md) for source management, diagnostics and offline recovery.

## Hermes and secrets

Hermes configuration and skills live in [system/programs/hermes/](system/programs/hermes).
Open the CLI with `hermes` in Nushell, or run `sudo -u hermes -i hermes` from another shell.
Its container endpoint and data handling are described in [Hermes containers](docs/operations.md#hermes-containers).

Secrets use sops-nix. System services decrypt with the root-only Age identity at `/persist/secrets/age/system.key` by default, so put the key in place before the first rebuild.
The [scripts guide](scripts/README.md#system-age-keypy) covers adopting an existing `age.txt` or `keys.txt`, generating a new identity, writing its recipient to `.sops.yaml`, and re-encrypting existing files.
Keep the private key outside Git and back it up offline.
Set `my.secrets.systemAgeKey.enable = false` to fall back to `~/.config/sops/age/keys.txt`.

Edit system secrets as root, with the system identity selected explicitly:

```sh
sudo SOPS_AGE_KEY_FILE=/persist/secrets/age/system.key \
  sops edit system/secrets/secrets.yaml  # API and messaging credentials
sudo SOPS_AGE_KEY_FILE=/persist/secrets/age/system.key \
  sops edit system/secrets/soul.yaml     # Hermes persona
sudo SOPS_AGE_KEY_FILE=/persist/secrets/age/system.key \
  sops edit system/secrets/proxy.yaml    # Proxy source catalog
```

Saving as root changes the file owner; restore it with `sudo chown "$USER": ...` before committing.
Rebuild to apply the changes.
Secret names and service ownership are declared in [sops.nix](system/programs/sops.nix).

## Repository guide

| Directory | Contents |
|---|---|
| [system/](system) | System services, hardware, network policy and packages |
| [home/](home) | Home Manager modules and user applications |
| [overlays/](overlays) | Package overrides |
| [local-data/](local-data) | Versioned inputs such as the DNS blocklist snapshot |
| [maintenance/](maintenance) | Repository-wide update inventory and review commands |
| [scripts/](scripts) | Configuration copy tools and blocklist refresh utility |
| [tests/](tests) | Package, policy and isolated VM checks |
| [docs/](docs/README.md) | Architecture, configuration, operations and maintenance |

Run `python3 scripts/check-maintenance.py` to validate the update inventory.
For changes to the network stack, use the [maintenance guide](docs/maintenance.md) to choose the relevant checks and review local patches before updating dependencies.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
