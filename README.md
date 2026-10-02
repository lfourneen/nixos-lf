# NixOS Configuration

English | [简体中文](README_CN.md)

A declarative NixOS system configuration using Nix flakes, featuring a customized desktop environment and various productivity tools.

---

## ⚠️ NOTICE / WARNING ⚠️

> **Do NOT blindly apply this configuration!**

* **Change the username**: Make sure to update all references to my username (`lfour`) to your own throughout the configs.
* **Hardware Partitioning**: This config uses `disko`, so you'll need to edit `system/hardware/disko.nix` to match your disk layout. (See [this clean install example](https://github.com/lfourneen/nixos-disko-lf).)
* **Adapt to Your Needs**: This config is tailored for my hardware and preferences. Review and adjust all settings before use!

---

## Features

* **Desktop Environment**: See `system/config/desktop.nix`
* **File System**: BTRFS with LUKS2
* **Shell**: Nushell with custom config
* **System Management**: Home Manager for user configuration
* **Hardware Support**: NVIDIA, AMDGPU, Bluetooth, audio, and other hardware configurations
* **Security**: SSH, firewall, and security hardening
* **Virtualization**: Docker and other virtualization tools

---

## Proxy Architecture

Outbound traffic is handled by a chain of local components:

| Component | Port / interface | Role |
|---|---|---|
| Clash Verge (mihomo) | `127.0.0.1:7897`, TUN device `Mihomo` | proxy core and DNS resolver |
| `gost-pac` (uid 987) | `127.0.0.1:33332` (HTTP), `127.0.0.1:33333` (transparent redirect) | local relay; forwards to mihomo while Clash runs, plain passthrough when it does not |
| `dnsmasq` | `127.0.0.1:1054` | single DNS entry point for the system |
| `unbound` | `127.0.0.1:1055` | encrypted (DoT) resolver used while mihomo is unavailable |
| nftables | — | kill switch, transparent redirect |

Applications that honour proxy settings are pointed at `127.0.0.1:33332` (session environment variables, GSettings, Flatpak overrides). TCP traffic from applications that ignore them is redirected to `127.0.0.1:33333` by nftables.

### Modes

`proxy-mode` loads the kill switch and the `:33333` redirect only while Clash runs, so the machine has two states with different guarantees:

| | **Mode A — Clash Verge closed** (the normal state on this machine) | **Mode B — Clash running, TUN up** |
|---|---|---|
| how you get there | close Clash Verge | start Clash Verge (`my.proxy.tunMode` decides whether Nix wants a TUN; the GUI decides whether one is created) |
| what is enforced | `nftables.service` loads `table inet filter` at boot with no dependency on Clash: `input policy drop`, the ct-state rules, the martian drop, the public-IPv6 drop, the `forward` chain and the masquerade pair. DNS goes to dnsmasq (:1054) and on to the encrypted DoT resolver (unbound :1055) | all of the above, plus: the TUN carries locally generated flows, mihomo's `dns-hijack` owns :53 for TUN-routed traffic, and the `proxymode_*` fragment adds the kill switch (non-root egress to public addresses on the uplinks is dropped), the `:33333` redirect and the guest policy |
| what it guarantees | a direct connection with the real address and no kill switch; portal/LAN/CGNAT addresses stay reachable by design, and the DHCP plaintext resolvers are an accepted fallback inside two bounded windows (see DNS below) | every locally generated flow goes through mihomo or is dropped; if the core, the TUN or the node fails, DNS stops and egress is refused instead of falling back to a direct connection; hotspot and VM clients are refused rather than relayed |
| what it does not protect | the source address and every destination are visible to the ISP and to anyone on the path; plaintext protocols stay readable; DNS filtering is a mitigation, not a wall; applications with their own DoH/DoT bypass this resolver chain | anything the profile sends DIRECT leaves from the real address; source- and device-bound sockets escape the TUN and are only contained by the kill switch; non-root UDP `3478/5349` (STUN/TURN) is dropped; a manual `sudo nft flush ruleset` removes every rule until the next `nftables.service` start |

Mode A does not conceal the source address and does not encrypt what
applications send. Mode B is only as strong as the checks that can see it, so
`nftables-verify` reads kernel state — the TUN device, mihomo's own
`table inet mihomo`, the FIB rules, the listeners, the live uid — instead of
trusting the status files, and a failure raises an alert through
`journalctl -t netsec-alert` and `/run/netsec/failed`.

With Clash closed the host is plain direct-connected: the portal, DNS and ordinary browsing work with no warm-up, and `gost-pac` keeps serving `:33332` as a plain passthrough instead of refusing, so applications that only honour proxy variables (nix-daemon, Flatpak apps, hermes, curl) keep working. While Clash runs, `gost-pac` only ever forwards to `127.0.0.1:7897`: a missing core or node fails the connection instead of falling back to a direct one, and the mode is selected from the Clash core inside `clash-verge.service`'s cgroup rather than from a process name, so a crashed core stays fail-closed.

The supervisors are event-driven. `proxy-net-watch.path` watches mihomo's control socket (and its directory) and `proxy-net-wake.service` wakes `proxy-mode`, `dns-pac` and `gost-pac` together when the core starts or stops; a NetworkManager dispatcher hook does the same on a link change. Each loop keeps an adaptive backstop — fast while a transition or a degradation is in flight, up to 10/15/30 s once settled — so a switch is handled in the same instant and the steady state costs almost nothing.

The recorded states are `/run/proxy-mode/status` (`proxy` | `direct` | `unenforced`) and `/run/gost-pac/status`, `/run/dns-pac/status` + `/run/dns-pac/reason` (shown by the `proxy-status` command in the Nushell config). `unenforced` means Clash is running and the enforcement fragment did not load: `nftables-verify` treats it as a failure, and `nftables.service`, `proxy-mode.service` and `nftables-verify.service` all alert through `netsec-alert@` when they fail. They describe the supervisors' state, not the path an individual connection takes.

### DNS

`systemd-resolved` uses dnsmasq on `127.0.0.1:1054`, and what dnsmasq forwards to depends on the mode. There is one resolver per mode and no permanent second server to fall through to:

* Mode B: `127.0.0.1:1053` (mihomo). The answers come from the DoH upstream mihomo forwards to, which validates DNSSEC: `dig +dnssec @127.0.0.1 -p 1053 cloudflare.com` carries the `ad` flag and an RRSIG, and `dnssec-failed.org` returns SERVFAIL. Since nothing else is configured, that refusal reaches the client instead of being replaced by a non-validating answer. `nftables-verify` checks it, once `dns-pac` has actually put the resolver on `:1053` (its status can lag `proxy-mode`'s): a random label under `dnssec-failed.org` has to be refused by `:1054` (a SERVFAIL or a timeout both count) while a control name resolves, retrying to ride out the asynchronous dnsmasq restart.
* Mode A: `127.0.0.1:1055` (unbound DoT to AliDNS). unbound does not validate (`enableRootTrustAnchor = false`, and the upstream strips RRSIGs), so Mode A has no DNSSEC protection; enabling validation with this upstream makes every signed name SERVFAIL, which is why it stays off.
* Mode A, if the encrypted hop is unreachable: the DHCP resolvers (read with `dhcpcd -U`) are appended only inside two bounded, logged windows — a 120 s bootstrap window after the link comes up or the encrypted hop fails, and while NetworkManager reports `portal`/`limited`. Outside them DNS stops and says why in `/run/dns-pac/reason` rather than becoming plaintext. `touch /run/dns-pac/force-plaintext` overrides this by hand.

Any plaintext resolver that is not dnsmasq's own upstream socket is redirected into dnsmasq (`:53` from the uplinks, IPv4 and IPv6, in both modes), so a hardcoded resolver in a portal/LAN/CGNAT range cannot leave in the clear.

### Notes and limitations

* The `:33333` redirect carries every TCP flow that is not routed into the TUN (source- and device-bound sockets, and anything after a manual flush), so `gost` is a single point of failure for those flows; `nftables-verify` checks that its listener is up. Flows that arrive through it are re-sourced to `127.0.0.1`, so per-source profile rules do not apply to them.
* Only `unbound` (DoT, tcp/853) and `systemd-timesyncd` (udp/123) may reach the network directly for DNS and NTP. Plain queries to other public resolvers are dropped for processes outside that exempt set *unless* they are redirected into dnsmasq first.
* The kill switch drops direct egress from any process that is not the proxy core, except loopback, LAN addresses, DNS/NTP, DHCP and multicast. The core is exempted by the packet mark it sets on its own sockets (`routing-mark`, pinned in the merge template and asserted by `nftables-verify`), not by uid: unmarked root traffic is dropped or redirected like anything else, so a core that is alive but not capturing cannot leak root egress. Explicit exceptions keep DHCP (`dhcpcd`) and the tailnet working.
* `gost-pac` is excluded from the `:33333` redirect so that its passthrough relay cannot dial itself; its egress is still subject to the kill switch, which drops it whenever proxy mode is loaded (a stale mode costs a moment of refused traffic, never a direct leak).
* A probe is only satisfied by a listener that belongs to `clash-verge.service`: the check reads the listener's cgroup, which a local process cannot forge, so merely listening on `127.0.0.1:7897` cannot attract traffic. The "Clash is on" decision requires the **core** in that cgroup; a GUI process alone no longer counts.
* The mihomo external controller is a world-writable unix socket that does not enforce a secret, so any process running as the login user can reconfigure the core — including setting a node to DIRECT. Accepted for a single-user desktop; the merge profile cannot override the controller settings.
* The probes require both a domestic name (resolved DIRECT) and one carried by the proxy group, so a failed probe means the chain cannot carry traffic, not merely that one upstream node is down.
* `systemctl stop nftables.service` no longer removes the firewall: the teardown is the same `nft -f` transaction as the rules, and the module's deletions file is empty, so the loaded rules stay until the next start replaces them. The manual reset remains `sudo nft flush ruleset` (which also removes mihomo's own table until the TUN restarts).
* Guests and VMs: forwarded traffic never traverses the output chain and carries no uid, so the kill switch cannot see it. While Clash runs, `proxymode_forward` refuses guest traffic that would leave an uplink for a public destination (portal/LAN/CGNAT stays reachable); with Clash closed the hotspot/VM rules apply as before. Guest traffic is never proxied — it is carried by the TUN or refused.
* Mode-A posture (`my.hardening.*`): the hotspot AP is **off** by default (its accepts are source-subnet based and therefore spoofable while `wlo1` is a client — enable it deliberately to share the uplink), and the tailnet may reach only the ports listed in `my.hardening.tailnet{Tcp,Udp}Ports` instead of every wildcard listener. The listening-capability grant (`dumpcap`/`usbmon`) is off: capture needs `sudo dumpcap`. The wireless connection keeps NetworkManager's own MAC policy unless `my.hardening.wifi.clonedMacAddress` is set (for example `stable`).
* `nftables-verify` only checks and never mutates state; repair is explicit and opt-in: `systemctl start nftables-verify-repair.service`.
* Documented trade-offs: Mode A keeps a bounded, logged plaintext DHCP-resolver window (see DNS above); in Mode B, `100.64.0.0/10` (and the tailnet) stay directly reachable by design through the early `local4` accept; and the A→B window is irreducible (enforcement trails the core) but bounded by the supervisors' backstop interval.

### Repository notes

* `system/programs/ssh.nix` is not imported (`system/programs/default.nix`), so no sshd unit and no `:22` listener are deployed. Enabling it also requires the `tcp dport 22` rule in `system/config/network.nix`.
* `gost` runs from its store path only (`gost-pac` uses absolute paths): it is not on the main system PATH and is no longer copied into the initrd.

---
## Usage

1. Clone this repository:
```bash
git clone https://github.com/yourusername/nixos-lf.git
cd nixos-lf/scripts/ && ./push-to-dir.sh

```

2. Update the config files as needed, for example:
* In `system/config/user.nix`, change `"lfour"` to your username.


3. Build and switch to the new configuration:
```bash
sudo nixos-rebuild switch --flake .#yourname

```

*(Replace `yourname` with your actual hostname or the flake output you wish to deploy.)*

---

## Directory Structure

```
.
├── flake.nix               # Main flake configuration
├── flake.lock              # Flake lock file
├── home/                   # User configuration
│   ├── config/             # User-specific configs
│   ├── programs/           # User programs
│   ├── wallpapers/         # Wallpaper files
│   └── userpkgs/           # User packages
│
├── overlays/               # Nixpkgs overlays
│   └── local_apps/         # Custom local applications
│
├── scripts/                # Utility scripts
│   ├── sync-to-git.sh      # Copy /etc/nixos to ~/Projects/Nix/nixos with relaxed permissions
│   └── push-to-dir.sh      # Push repo config back to /etc/nixos with secure permissions
│
└── system/                 # System-wide configuration
    ├── config/             # System configs
    ├── hardware/           # Hardware-specific configurations
    ├── modules/            # Kernel modules configurations
    ├── programs/           # System programs and services
    ├── secrets/            # Encrypted secrets (sops)
    └── systempkgs/         # System packages

```

---

## Scripts

* **`scripts/sync-to-git.sh`** — Copies `/etc/nixos` to `~/Projects/Nix/nixos` (creating `~/Projects/Nix` if needed), changes ownership to the current user, and sets permissive permissions (dirs 755 / files 644) so the config can be committed to Git.
* **`scripts/push-to-dir.sh`** — Reverse of the above. Copies `home/`, `overlays/`, `system/`, and `flake.nix` from the repo into `/etc/nixos` and applies secure permissions (dirs 700 / files 600). Must be run with `sudo`.

---

## Hermes / Sops-Nix

This configuration manages `hermes` secrets using `sops-nix` encrypted with an Age key pair.

### 1. Age Key Setup

Choose **Option A** if you are restoring/migrating an existing system, or **Option B** if you are setting this up for the first time.

#### Option A: Migration / Restoring Existing Setup (Recommended)
If you already have a backed-up Age key pair, simply copy your `keys.txt` to the expected location:

```bash
mkdir -p ~/.config/sops/age
cp /path/to/your/backup/keys.txt ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

```

#### Option B: Fresh Initial Setup

If you are generating a new key pair for a brand-new configuration:

1. Generate a new Age key:
```bash
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt

```

> ⚠️ **Security Warning:** `keys.txt` is your private key. Never commit it to Git or expose it publicly! Store a backup in a secure location.

2. Get your public key from the output or by inspecting the file:
```bash
# Public key format: age1...

```

3. Create or update `.sops.yaml` in your configuration root directory:
```yaml
creation_rules:
  - path_regex: secrets\.yaml$
    key_groups:
      - age:
          - "age1ql30gw8xxxxxxxxxxxxxxxxxxxxxxxxxxx" # Paste your public key here

```

4. Create and edit your encrypted secret file:
```bash
cd /etc/nixos/system/secrets
sops secrets.yaml

```

Add your API keys in YAML format (e.g., `hermes_api_key: "sk-proj-1234567890abcdef"`). Upon saving, the file contents will be automatically encrypted.

> Adding a new key later (all keys are top-level YAML entries):

```bash
cd /etc/nixos/system/secrets

# From the command line. Key uses bracket/index syntax, value must be
# a valid JSON string (extra quotes around it):
sops set secrets.yaml '["github_token"]' '"ghp_xxx"'

# From a file (long values, e.g. ssh private key)
sops set --value-file secrets.yaml '["ssh_host_ed25519_key"]' /tmp/key

# Or from stdin
echo -n 'ghp_xxx' | sops set --value-stdin secrets.yaml '["github_token"]'

# Interactive: opens the decrypted file in your editor, save to re-encrypt
sops secrets.yaml

# Verify
sops -d secrets.yaml
```

### 2. Running Hermes

After deploying the NixOS configuration (`update`), launch Hermes using either method:

```bash
# Nushell helper (runs 'sudo -u hermes -i hermes' with sandbox notice)
hermes

# Plain bash
sudo -u hermes -i hermes

```

---

## Customization

* **System Config**: Edit files in `system/config/`
* **User Config**: Edit files in `home/config/`
* **Programs**: Modify `system/programs/` and `home/programs/`
* **Hardware**: Adjust settings in `system/hardware/`
* **Applications**: See `home/userpkgs/`, `overlays/` and `system/systempkgs/`

---

## Dependencies

* NixOS 26.05 && Unstable
* Home Manager
* Noctalia shell
* Disko
* Impermanence
* Nix-Flatpak
* Hermes Agent
* Sops-Nix
* MCP-NixOS
* LLM-Agents

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
