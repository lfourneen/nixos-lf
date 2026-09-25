# NixOS Configuration

English | [简体中文](README_CN.md)

A declarative NixOS system configuration using Nix flakes, featuring a customized desktop environment and various productivity tools.

---

## ⚠️ NOTICE / WARNING ⚠️

> **Do NOT blindly apply this configuration!**

* **Change the username**: Make sure to update all references to my username (`lfour`) to your own throughout the configs.
* **Hardware Partitioning**: This config uses `disko`, so you'll need to edit `system/hardware/disko.nix` to match your disk layout. (See [this clean install example](https://github.com/LFour86/nixos-disko-lf).)
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

`proxy-mode` loads the kill switch and the `:33333` redirect only while Clash runs. With Clash closed the host is plain direct-connected: the portal, DNS and ordinary browsing work with no warm-up, and `gost-pac` keeps serving `:33332` as a plain passthrough instead of refusing, so applications that only honour proxy variables (nix-daemon, Flatpak apps, hermes, curl) keep working. While Clash runs, `gost-pac` only ever forwards to `127.0.0.1:7897`: a missing core or node fails the connection instead of falling back to a direct one, and the mode is selected from the Clash unit and GUI rather than from the core, so a crashed core stays fail-closed. DNS follows the same split: `dns-pac` repoints dnsmasq at the encrypted resolver while mihomo is down.

`/run/gost-pac/status` and `/run/dns-pac/status` (shown by the `proxy-status` command in the Nushell config) report the mode the supervisors have selected; they describe the supervisors' state, not the path an individual connection takes.

### DNS

`systemd-resolved` uses dnsmasq on `127.0.0.1:1054` and falls back to unbound on `127.0.0.1:1055`. With the TUN device up, mihomo takes over port 53 and resolves through the DNS section of the Clash Verge merge profile; dnsmasq and unbound then serve the fallback window.

### Notes and limitations

* With the TUN device up, the nftables redirect to `:33333` is dormant: TCP is intercepted by mihomo's own auto-redirect rules first. The redirect carries traffic while the TUN is down.
* Only `unbound` (DoT, tcp/853) and `systemd-timesyncd` (udp/123) may reach the network directly for DNS and NTP. Plain queries to other public resolvers are dropped for processes outside that exempt set.
* The kill switch drops direct egress from non-root processes, except loopback, LAN addresses, DNS/NTP and multicast. Only root is exempt, and that exemption is what lets mihomo's DIRECT rule egress directly while proxy mode is loaded: the DIRECT availability of the chain implicitly depends on mihomo running as root.
* `gost-pac` is excluded from the `:33333` redirect so that its passthrough relay cannot dial itself; its egress is still subject to the kill switch, which drops it whenever proxy mode is loaded (a stale mode costs a moment of refused traffic, never a direct leak).
* A probe is only satisfied by a listener that belongs to `clash-verge.service`: the check reads the listener's cgroup, which a local process cannot forge, so merely listening on `127.0.0.1:7897` cannot attract traffic.
* The mihomo external controller is a world-writable unix socket that does not enforce a secret, so any process running as the login user can reconfigure the core — including setting a node to DIRECT. Accepted for a single-user desktop; the merge profile cannot override the controller settings.
* The probes require both a domestic name (resolved DIRECT) and one carried by the proxy group, so a failed probe means the chain cannot carry traffic, not merely that one upstream node is down.

### Repository notes

* `system/programs/ssh.nix` is not imported (`system/programs/default.nix`), so no sshd unit and no `:22` listener are deployed. Enabling it also requires the `tcp dport 22` rule in `system/config/network.nix`.
* `gost` is installed into the initrd (`boot.initrd.systemd.extraBin`) instead of `environment.systemPackages`, so it is not on the main system PATH. `gost-pac` uses the store path directly; `curl` and `sed` are still provided by nixpkgs' default packages.

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
