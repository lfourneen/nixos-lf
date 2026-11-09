# Network documentation

English | [简体中文](CN/README_CN.md) | [Project](../README.md)

This system combines physical uplinks, local DNS, a managed proxy core, guest networking and a native Firefox package. The network mode determines which paths applications may use; browser privacy and proxy routing are separate choices.

| Guide | Contents |
|---|---|
| [Network architecture](network.md) | Traffic paths, A/B/C modes, firewall, uplinks, guests and Tailscale |
| [Direct egress](egress.md) | Exception ledger, optional restricted B grants, time-server pins and C blocking |
| [DNS](dns.md) | Resolver responsibilities, shared domain filter, DNSSEC, optional strict encryption, recovery and split DNS |
| [Proxy core and permissions](proxy.md) | Configuration authorization, service identities, controller API and routing policy |
| [Firefox](browser.md) | Managed launcher, network paths, DNS, ECH and privacy settings |
| [Operations](operations.md) | Switching modes, status, recovery, configuration and checks |
| [Patch maintenance](maintenance.md) | Customization inventory, build integration, component upgrades and validation |

Start with the architecture guide to understand the system. Use Operations for day-to-day administration and Patch maintenance before updating dependencies.

Defaults retain direct access at boot (A), user-requested proxy access (B), blocking on proxy failure (C), subscription Rule routing and LAN access. `proxyOnly` can require application proxy routing inside the core; `bootMode` independently controls whether boot starts with direct access or blocking.

The guides describe the configuration in this repository. Interface names, UIDs and ports shown are its defaults; their definitions live in [machine.nix](../system/config/machine.nix). `docs/` stays in the checkout and is excluded from the deployment assembled by [push-to-dir.sh](../scripts/push-to-dir.sh).
