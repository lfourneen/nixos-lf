# Source layout

English | [简体中文](CN/layout_CN.md) | [Documentation](README.md)

System services and policy live under `system/`, while Home Manager modules under `home/` configure the desktop and user applications. Each module directory uses `default.nix` to list its imports, keeping actual packages, service definitions, and settings inside the imported modules. The overlay entry uses a direct import to expose the package overlay function.

## System and desktop responsibilities

| Directory | Responsibility |
|---|---|
| [`system/config/network/`](../system/config/network) | Network declarations and their runtime helpers, imported through `default.nix` |
| [`system/programs/clash/`](../system/programs/clash) | System-wide Clash Verge packaging, GUI launch integration, privileged helper, and Mihomo build |
| [`system/programs/gost/`](../system/programs/gost) | Gost packaging, HTTP patch, and relay service |
| [`system/programs/hermes/`](../system/programs/hermes) | Agent configuration, `hermes-skills/`, mail CLI, and restricted rootless Docker integration |
| [`home/config/clash/`](../home/config/clash) | Home Manager user profile files and the English status renderer |
| [`home/programs/brush.nix`](../home/programs/brush.nix) | Desktop user's interactive Brush commands and environment |
| [`home/programs/firefox/`](../home/programs/firefox) | Managed browser preferences, launcher, and desktop sandbox |
| [`system/programs/systemd/`](../system/programs/systemd) | Global journald and coredump settings |
| [`system/hardware/`](../system/hardware) | Device configuration and associated boot, CPU, and NVIDIA service settings |

The status renderer in `home/config/clash/status.py` reads the shared readiness contract defined in `system/config/network/proxy/state.py`. The diagnostics module provides `proxy-status` to interactive shells and scripts. Privileged configuration import and snapshot authorization stay on the system side with the Clash service.

## Network feature

`system/config/network/default.nix` ties the network stack together. Host and guest access options are defined in `options.nix`, managed proxy options in `proxy/options.nix`, and machine-specific identities—interface names, UIDs, and ports—all come from `system/config/machine.nix`.

| Subdirectory | Contents |
|---|---|
| `dns/` | DNS upstream coordination, leases and budgets, DNSSEC probes, blocklist, and NetworkManager/resolved integration |
| `proxy/` | Root profile, A/B/C coordinator, health checks, shared readiness, listener checks, TUN observations, fixed core namespace/veth, and localhost broker |
| `firewall/` | Bootstrap rules, ordered-rule verification, alerts, recovery, and direct-egress checks |
| `guests/` | Routed and host-socket guest policy |
| `identity/` | Link monitoring, profile identity preparation, and restore |
| `tailscale/` | Transport source contract |
| `diagnostics/` | Status/inspection command integration and passive resource collection |
| `maintenance/` | Selected-source inventory and component check planning |
| `lib/` | Shared address classes |

## Clash and Gost build inputs

Under `system/programs/clash/`, `gui/` handles system-package launch and defaults, `service/` holds the helper patch, service identity, core launcher, authorized snapshot stage tool, and preparation check, and `mihomo/` groups build inputs into `patches/`, `guard/`, `config/`, `controller/`, and `tests/`. The package fileset collects those payloads for the build-time patchers.

Gost keeps its own `patches/`, `src/`, and `tests/` inside `system/programs/gost/`. [package.nix](../system/programs/gost/package.nix) builds the patched Gost binary that [relay.nix](../system/programs/gost/relay.nix) runs. See [patch maintenance](maintenance.md) for source entrypoints and validation commands.

## Tests

| Directory | Scope |
|---|---|
| [`tests/network/`](../tests/network) | Proxy, DNS, firewall, guest, identity, Tailscale, time, integration, unit, and maintenance checks |
| [`tests/home/clash/`](../tests/home/clash) | GUI defaults and process handling |
| [`tests/home/firefox/`](../tests/home/firefox) | Browser policy, runtime, sandbox, and ECH fixtures |
| [`tests/deployment/`](../tests/deployment) | Deployment-copy checks |

[tests/checks.nix](../tests/checks.nix) exposes these checks under `checks.x86_64-linux.*`. See [maintenance](maintenance.md#validation) for which checks to run and the exact commands.
