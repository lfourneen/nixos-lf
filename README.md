# NixOS Configuration

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
│   ├── sync-to-git.sh      # Copy /etc/nixos to ~/Downloads/nixos with relaxed permissions
│   └── push-to-dir.sh      # Push repo config back to /etc/nixos with secure permissions
│
└── system/                 # System-wide configuration
    ├── config/             # System configs
    ├── hardware/           # Hardware-specific configurations
    ├── modules/            # Kernel modules configurations
    ├── programs/           # System programs and services
    └── systempkgs/         # System packages

```

---

## Scripts

* **`scripts/sync-to-git.sh`** — Copies `/etc/nixos` to `~/Downloads/nixos`, changes ownership to the current user, and sets permissive permissions (dirs 755 / files 644) so the config can be committed to Git.
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
cd /etc/nixos/system/programs/secrets
sops secrets.yaml

```

Add your API keys in YAML format (e.g., `hermes_api_key: "sk-proj-1234567890abcdef"`). Upon saving, the file contents will be automatically encrypted.

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

* NixOS 26.05 and unstable
* Home Manager
* Noctalia shell
* Disko
* Impermanence
* Nix-Flake
* Hermes Agent
* Sops-Nix
* MCP-NixOS

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
