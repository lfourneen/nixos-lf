# Network documentation

English | [简体中文](CN/README_CN.md) | [Project](../README.md)

These guides cover the network stack: traffic routing, DNS, proxy access, Firefox and guest networks.

| Guide | Contents |
|---|---|
| [Source layout](layout.md) | System/desktop ownership, network modules, build payloads and test directories |
| [Network architecture](network.md) | Traffic paths, A/B/C modes, firewall, uplinks, guests and Tailscale |
| [Link identity](identity.md) | MAC defaults, per-profile identities, coordinated DHCP/IPv6 migration, wired policy and persisted state |
| [Direct egress](egress.md) | Exception ledger, optional restricted B grants, time-server pins and C blocking |
| [DNS](dns.md) | Resolver responsibilities, shared domain filter, DNSSEC, optional strict encryption, recovery and split DNS |
| [Proxy core and permissions](proxy.md) | Configuration authorization, service identities, controller API and routing policy |
| [Firefox](browser.md) | Managed launcher, outer sandbox, network paths, DNS, ECH and privacy settings |
| [Operations](operations.md) | Switching modes, status, recovery, configuration and checks |
| [Network patch maintenance](maintenance.md) | Customization inventory, build integration, source compatibility checks and component regression plans |

Start with [Network architecture](network.md) for traffic paths and the A/B/C modes.
For commands and troubleshooting, use [Operations](operations.md).
[Patch maintenance](maintenance.md) covers dependency updates and local customizations.

The default policy starts with direct access in A.
Requesting proxy access selects B once the core is ready, or C while it is unavailable.
`proxyOnly` controls routing inside the core, and `bootMode` controls the initial mode.
Their configuration is described in the linked guides.
Interface names, service UIDs and ports come from [machine.nix](../system/config/machine.nix).
