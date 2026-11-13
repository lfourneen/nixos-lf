# Source layout

English | [简体中文](CN/layout_CN.md) | [Documentation](README.md)

`tests/checks.nix` owns check definitions and test configurations; the root `flake.nix` imports it. `system/programs/clash/service/source_worker.py`, `source_broker.py` and `source.nix` own controlled sources; `strict-browser.nix` and `home/programs/firefox/network.py` connect the strict browsing path.

## System and desktop responsibilities

| Directory | Responsibility |
|---|---|
| [`system/config/network/`](../system/config/network) | Network declarations and their runtime helpers, imported through `default.nix` |
| [`system/programs/clash/`](../system/programs/clash) | System-wide Clash Verge packaging, GUI launch integration, privileged helper and Mihomo build |
| [`system/programs/gost/`](../system/programs/gost) | Gost packaging, HTTP patch and relay service |
| [`home/config/clash/`](../home/config/clash) | Home Manager's user profile files and the English status renderer |
| [`home/programs/firefox/`](../home/programs/firefox) | Managed browser preferences, launcher and desktop sandbox |
| [`system/programs/systemd/`](../system/programs/systemd) | Global journald and coredump settings |
| [`system/hardware/`](../system/hardware) | Device configuration and associated boot, CPU and NVIDIA service settings |

The status renderer in `home/config/clash/status.py` consumes the shared readiness contract in `system/config/network/proxy/state.py`. The diagnostics module exports its command for interactive shells and automated consumers. Nushell forwards `proxy-status` arguments to that command. Privileged configuration import and snapshot authorization belong to the system-side Clash service.

## Network feature

`system/config/network/default.nix` assembles the feature. `options.nix` defines host/guest access options; `proxy/options.nix` defines managed proxy options. Machine identities, interface names, UIDs and ports come from `system/config/machine.nix`.

| Subdirectory | Contents |
|---|---|
| `dns/` | DNS upstream coordination, leases and budgets, DNSSEC probes, blocklist and NetworkManager/resolved integration |
| `proxy/` | Root profile, A/B/C coordinator, health, shared readiness, listener checks and TUN observations |
| `firewall/` | Bootstrap, ordered-rule verification, alerts, recovery and direct-egress checks |
| `guests/` | Routed and host-socket guest policy |
| `identity/` | Link monitoring, profile identity preparation and restore |
| `tailscale/` | Transport source contract |
| `diagnostics/` | Status/inspection command integration and passive resource collection |
| `maintenance/` | Selected-source inventory and component check planning |
| `lib/` | Shared address classes |

## Clash and Gost build inputs

Clash's `gui/` contains system package launch/default handling. `service/` contains the helper patch, service identity, core launcher, authorized snapshot stage tool and preparation check. `mihomo/` groups build inputs into `patches/`, `guard/`, `config/`, `controller/` and `tests/`. The package fileset collects these payloads for the build-time patchers.

Gost uses `patches/`, `src/` and `tests/` under its own program directory. Its default module connects the package to the relay service. See [patch maintenance](maintenance.md) for source entrypoints and validation commands.

## Tests

| Directory | Scope |
|---|---|
| [`tests/network/`](../tests/network) | Proxy, DNS, firewall, guest, identity, Tailscale, time, integration, unit and maintenance checks |
| [`tests/home/clash/`](../tests/home/clash) | GUI defaults and process handling |
| [`tests/home/firefox/`](../tests/home/firefox) | Browser policy, runtime, sandbox and ECH fixtures |
| [`tests/deployment/`](../tests/deployment) | Deployment-copy checks |

`tests/checks.nix`, imported by `flake.nix`, connects these sources to `checks.x86_64-linux.*`. Nix tests pass source paths explicitly when scripts execute as standalone store files; local Python tests locate the repository from their feature directory.
