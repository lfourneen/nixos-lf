# Network documentation

English | [简体中文](CN/README_CN.md) | [Project](../README.md)

These guides document how the workstation's network stack fits together—traffic routing, DNS, transparent proxying, Firefox isolation, and guest networks.

| Guide | Contents |
|---|---|
| [Source layout](layout.md) | System/desktop ownership, network modules, build payloads, and test directories |
| [Network architecture](network.md) | Traffic paths, A/B/C modes, firewall, uplinks, guests, and Tailscale |
| [Link identity](identity.md) | MAC defaults, per-profile identities, coordinated DHCP/IPv6 migration, wired policy, and persisted state |
| [Direct egress](egress.md) | Exception ledger, optional restricted B grants, time-server pins, and C blocking |
| [DNS](dns.md) | Resolver responsibilities, shared domain filter, DNSSEC, optional strict encryption, recovery, and split DNS |
| [Proxy core and permissions](proxy.md) | Configuration authorization, service identities, controller API, and routing policy |
| [Firefox](browser.md) | Managed launcher, outer sandbox, network paths, DNS, ECH, and privacy settings |
| [Operations](operations.md) | Switching modes, status, recovery, configuration, and checks |
| [Network patch maintenance](maintenance.md) | Customization inventory, build integration, source compatibility checks, and component regression plans |

If you are new to the setup, start with [Network architecture](network.md) for the traffic paths and A/B/C mode model. Keep [Operations](operations.md) handy for daily commands and troubleshooting, and check [Patch maintenance](maintenance.md) before bumping dependencies or editing local patches.

By default, the system boots into direct access (**A**). Requesting proxy access transitions to **B** once the core passes health checks, or holds in **C** if the core is unavailable. `proxyOnly` controls routing inside the core and `bootMode` controls the startup mode—both are covered in the linked guides, while interface names, service UIDs, and ports are centralized in [machine.nix](../system/config/machine.nix).
