# scripts

English | [简体中文](README_CN.md)

Repository-maintained helpers for deploying the flake, refreshing DNS data,
verifying the network policy, benchmarking, checking the maintenance ledger,
migrating VM ownership and creating a root-only Age key.

Run them from the repository root. They need Bash, coreutils, findutils,
rsync, util-linux (flock), Nix and Python 3; the Python tools use only the
standard library. The deployed copy
under `/etc/nixos/scripts` is a 0600 build-input snapshot, so use the scripts
from the checkout.

| Script | Purpose |
| --- | --- |
| `rebuild-deployed.sh` | Rebuild the deployed flake under the deployment lock |
| `push-to-dir.sh` | Copy the build payload into `/etc/nixos` with staging, validation, locking and a backup |
| `sync-to-git.sh` | Copy `/etc/nixos` into a private Git checkout without overwriting it |
| `refresh-dns-blocklist.py` | Fetch, validate and publish the DNS blocklist snapshot and manifest |
| `verify-network.py` | Run component checks and record exact inputs and outputs |
| `network-benchmark.py` | Measure controlled HTTP throughput and network latency by layer |
| `check-maintenance.py` | Validate the maintenance ledger against files, `flake.lock` and network sources |
| `libvirt-qemu-migrate.sh` | Give the unprivileged QEMU user ownership of VM images and state |
| `system-age-key.py` | Create or adopt the root-only Age identity for system secrets |
| `usbguard-policy-migrate.py` | Copy the installed USBGuard policy into private persistent storage |

## push-to-dir.sh

Copies the payload trees (`home/`, `overlays/`, `system/`, `tests/`,
`scripts/`, `maintenance/`, `local-data/`, the flake files and `.sops.yaml`)
into `/etc/nixos`. `--test-mode` permits a disposable `DEST_DIR`. It holds an exclusive lock,
builds a private staging copy, checks permissions and symlinks, evaluates the
staged flake with `nix eval --offline`, and only then syncs the tree. Files
are backed up as a complete tree with a recovery log. Failed installs restore
the previous tree, and the next deployment recovers interrupted transactions.
The final tree replacement is not atomic. Rebuild the deployed copy through
`sudo bash scripts/rebuild-deployed.sh switch` to hold the same lock.

A non-empty destination without the `.nixos-lf-deploy` marker is refused. An
existing `/etc/nixos` must be adopted once:

```sh
sudo ./scripts/push-to-dir.sh --adopt-existing
sudo ./scripts/push-to-dir.sh
```

An empty destination does not need the flag. After the first successful marked
deployment, later runs can omit `--adopt-existing`. The backup and recovery log
live under `/var/lib/nixos-lf-deploy`, which is persisted across reboots.

`--skip-eval` omits the staged `nix eval`. Use it for an offline copy of a
trusted checkout or when the flake inputs are not available locally. The file
permissions, manifest, lock and backup still run, and `nixos-rebuild` evaluates
the deployed tree before switching.

| Variable | Meaning |
| --- | --- |
| `DEST_DIR` | Destination tree; default `/etc/nixos` |
| `SUDO` | Privilege prefix; empty runs unprivileged against a throwaway destination |
| `DEPLOY_STATE_DIR` | Staging and backup root; default `/var/lib/nixos-lf-deploy`, or a per-user temporary directory when unprivileged |
| `DEPLOY_LOCK_FILE` | Lock file; default `/run/lock/nixos-lf-deploy.lock`, or a file under the state directory when unprivileged |
| `DEPLOY_SKIP_EVAL` | Set to `1` to skip the staged evaluation, equivalent to `--skip-eval` |

## USBGuard policy

USBGuard reads device rules from `/persist/usbguard/rules.conf`, owned by
root with mode 0600. The parent directory is root-owned with mode 0700.
Nix declares the service settings and policy path; device identifiers remain
in this private file. Keep the file in a private backup, outside Git.

### Migrate an existing policy

If an existing NixOS configuration supplies store-backed USBGuard rules,
copy them before the first switch:

```sh
sudo python3 scripts/usbguard-policy-migrate.py
```

The helper locates the daemon configuration through `usbguard.service`'s
`ExecStart` argument `-c`, then copies its store-backed `RuleFile` without
printing the rules. The service can be stopped, but its unit must still be
installed. An existing private policy is preserved. Activation also performs
this adoption before replacing `/etc`.

The migration helper requires existing rules. Use the following procedure
when USBGuard has never been configured.

### Enable USBGuard for the first time

1. Connect the trusted keyboard, mouse, hubs and development devices that must
   work immediately. Disconnect devices that should not enter the initial
   allowlist. `generate-policy` includes every connected device, so review its
   output before enabling the service.
2. Create the private directory and generate the initial policy. The command
   uses this flake's USBGuard package and does not require a running daemon or
   a globally installed CLI. It refuses to overwrite an existing rules file.

   ```sh
   sudo install -d -m 0700 -o root -g root /persist/usbguard
   sudo nix shell .#nixosConfigurations.lfour.pkgs.usbguard --command sh -c 'set -euC; umask 077; usbguard generate-policy > /persist/usbguard/rules.conf'
   sudoedit /persist/usbguard/rules.conf
   ```

   Keep only the intended devices. Generated rules include descriptor hashes
   and interface constraints; avoid replacing them with broad vendor-only or
   unconditional `allow` rules. Descriptor matching does not authenticate the
   physical device. Review the [upstream rule language](https://usbguard.github.io/documentation/rule-language)
   when changing attributes.
3. Deploy, build and switch using the commands in
   [push-to-dir.sh](#push-to-dirsh). The USBGuard module is already imported by
   this configuration. Perform the first switch with a local console available
   and the input devices covered by the reviewed policy.
4. Confirm the service is running and check that intended devices are allowed:

   ```sh
   sudo systemctl status usbguard.service
   sudo usbguard list-devices
   ```

The module applies the policy to devices already present at daemon startup
and to newly inserted devices. Devices without a matching allow rule are
blocked. Use the commands below for later additions; regenerating the initial
policy would replace prior decisions.

### Add a device or a custom rule

The CLI uses raw IPC, which this configuration reserves for administrators,
so the following commands use `sudo`. Desktop D-Bus queries are available to
active local users, while D-Bus changes require administrator authentication.

1. Plug in the new device and find its device ID:

   ```sh
   sudo usbguard list-devices --blocked
   ```

   Check its vendor/product, serial, hash and interfaces against the intended
   hardware. The device ID `42` below is an example; use the current output.
2. Allow it temporarily and test the required functions:

   ```sh
   sudo usbguard allow-device 42
   ```

3. Once checked, save the device-specific authorization permanently:

   ```sh
   sudo usbguard allow-device --permanent 42
   sudo usbguard list-rules
   ```

   This updates the live policy and `/persist/usbguard/rules.conf`. No rebuild
   or daemon restart is needed. Reconnect the device to check that its rule
   still matches. Device IDs can change after reconnection or daemon restart.

For a custom rule, replace the example attributes with the intended device's
actual values. `equals` requires the complete interface set to match:

```sh
sudo usbguard append-rule 'allow id 1234:5678 serial "DEVICE_SERIAL" with-interface equals { ff:00:00 }'
```

`append-rule` saves the rule by default; `--temporary` keeps it in memory.
Rules are evaluated in order, and the first match wins. An earlier matching
`block`, `reject` or broader `allow` rule must be addressed before a later rule
can affect that device. Inspect the current order with `list-rules`.

### Revoke an authorization

Device IDs from `list-devices` and rule IDs from `list-rules` are different.
Find the rule to remove, then block the connected device as well. Here `17`
is an example rule ID and `42` an example device ID:

```sh
sudo usbguard list-rules
sudo usbguard remove-rule 17
sudo usbguard block-device 42
```

Removing a rule updates the saved policy; blocking the device also revokes its
current access. Check for other matching allow rules when removing an exception.
Keep device identifiers in the private policy rather than adding them to
`services.usbguard.rules`, which would put them in the Nix store.

## sync-to-git.sh

Copies `SRC_DIR`, normally `/etc/nixos`, into a private checkout. The default
destination is `~/Projects/Nix/nixos-<timestamp>`, created with mode 0700. The
copy preserves file modes, symlinks and timestamps, so secret files stay
private and executable files keep their bits.

An existing non-empty destination is refused. `--force` moves it to
`<destination>.backup-<timestamp>` before copying. The script never deletes a
Git repository.

```sh
./scripts/sync-to-git.sh
./scripts/sync-to-git.sh --force
```

| Variable | Meaning |
| --- | --- |
| `SRC_DIR` | Source tree; default `/etc/nixos` |
| `DEST_BASE` | Parent for the timestamped destination; default `$HOME/Projects/Nix` |
| `DEST_DIR` | Explicit destination instead of a timestamped name |
| `SUDO` | Privilege prefix; empty copies unprivileged |

## refresh-dns-blocklist.py

Fetches the configured upstream lists, validates every name, merges and
de-duplicates them, then writes the compressed snapshot, the manifest and the
generated header in `system/config/network/dns/blocklist.nix`. The build
validates the snapshot against the manifest, so a malformed or mismatched
snapshot fails the configuration instead of producing a partial filter.

```sh
./scripts/refresh-dns-blocklist.py
./scripts/refresh-dns-blocklist.py --check
./scripts/refresh-dns-blocklist.py --check --fetch
./scripts/refresh-dns-blocklist.py --offline
```

| Option | Behavior |
| --- | --- |
| no option | Fetch, validate and publish the new data, manifest and header |
| `--check` | Verify the committed data, manifest and header; write nothing |
| `--check --fetch` | Also compare the committed data with the upstream sources |
| `--offline` | Rebuild the manifest and header from the committed snapshot |
| `--accept-large-change` | Publish a change above the review thresholds |
| `--json` | Print the result as JSON |

Exit codes are 0 for consistent or successfully published data, 1 for drift or
a refused large change, and 2 for invalid input. Downloads use HTTPS for every
redirect, enforce a host allowlist, a wall-clock deadline and a size cap.
Publication holds a writer lock and a durable rollback journal. A later refresh
recovers interrupted writes; verification refuses an unfinished publication.
Protected infrastructure domains cannot enter the suffix-blocking snapshot.

## verify-network.py

Runs the checks listed for selected network components and archives the exact
inputs and outcomes. Components come from
`system/config/network/maintenance/sources.json`: `browser-isolation`, `clash`,
`controlled-source`, `dns`, `firefox`, `firewall`, `gost`, `link-identity`,
`mihomo`, `sing-tun`, `socks` and `tailscale`.

```sh
./scripts/verify-network.py --plan --component dns
./scripts/verify-network.py --component dns --component firewall
./scripts/verify-network.py --all
./scripts/verify-network.py --component dns --frozen
```

The archive defaults to `audit/validation/<timestamp>` and holds
`results.json`, per-check logs and the lock file. It is created with mode 0700.
The manifest records the commit, the working-tree diff, build inputs including
untracked files, the lock hash, package versions and the derivation and output
of every check. By default the run fails when an untracked build input exists;
`--allow-untracked` records it as accepted instead.

| Option | Meaning |
| --- | --- |
| `--component NAME` | Select a component; repeat for more than one |
| `--all` | Select every component |
| `--plan` | Print the selected components and checks without running them |
| `--archive DIR` | Write evidence to a specific directory |
| `--online` | Allow checks that need the network |
| `--timeout SECONDS` | Per-check timeout |
| `--allow-untracked` | Include untracked build inputs and mark them accepted |
| `--runtime-verification` | Also require fresh successful nftables JSON for the active boot and policy |
| `--frozen` | Copy the exact input to a private temporary directory and run from it |

## network-benchmark.py

Measures controlled network workloads using the active policy.

```sh
./scripts/network-benchmark.py serve --bind 127.0.0.1 --port 18094
./scripts/network-benchmark.py measure --url http://127.0.0.1:18094 --samples 20
./scripts/network-benchmark.py layers --name origin.example.test --address 192.0.2.10 --samples 20
./scripts/network-benchmark.py snapshot --tool system/programs/clash/service/clash-verge-stage.py
```

`serve` starts a loopback HTTP fixture. `measure` reports P50 and P95
time-to-first-byte and total time, connections per second, bulk throughput and
client CPU and RSS for a native or explicit-proxy path.

`layers` requires `dig`, `ping` and a controlled HTTPS origin. Replace the example name and address with the origin's DNS name and IPv4 or IPv6 address. Use `--ca-file` for a private CA.
It queries DNS for the matching address family and records the response code, address records and DNS TTL.
It independently uses the supplied address for ICMP echo and HTTPS, with the name as the TLS and HTTP identity.
Add `--proxy` when the caller is authorized to use a controlled HTTP CONNECT listener.
The JSON includes each observation and P50, P95 and maximum by phase and outcome. TCP times the client socket handshake, which may terminate in the TUN or relay.
HTTPS success requires verified TLS and an HTTP 200 response with a body byte. `--timeout` bounds the full HTTPS sample and is also the DNS query and echo deadline.

`snapshot` measures the full and fast verification paths on a synthetic 32 MiB snapshot. `blocklist` measures decompression and Python
set construction for the committed domain snapshot.

## check-maintenance.py

Validates `maintenance/inventory.json`. The checker confirms the schema, unique
item ids, literal pins against repository files, root flake input revisions
against `flake.lock`, and `network_ref` entries against
`system/config/network/maintenance/sources.json`. Network entries inherit their
shared review instructions from `network_defaults`. For `nix_release` entries,
the checker reads the version and expected number of SHA-256 hashes from
the specified Nix file. The `--json` output includes these values under
`nix_releases`. The default run makes no network requests.

```sh
python3 scripts/check-maintenance.py
python3 scripts/check-maintenance.py --json
python3 scripts/check-maintenance.py --online --json
```

`--online` records current version, permissions and file digest from the official
Mozilla addon API. It does not install updates or verify downloaded XPI signatures.
`--snapshot FILE` writes private installed metadata for Firefox, Flatpak, Docker
and plugins. `--baseline FILE` fails when those observations change; unavailable
runtimes are recorded explicitly. Docker observations record digests without
verifying publisher signatures. The ledger also checks the entire flake lock
graph against `lock_graph_sha256`.

```sh
python3 scripts/check-maintenance.py --snapshot /private/path/runtime.json
python3 scripts/check-maintenance.py --baseline /private/path/runtime.json --json
```

## libvirt-qemu-migrate.sh

Gives the unprivileged `qemu-libvirtd` user ownership of the libvirt disk
images and persistent QEMU state. Only `<root>/images` and `<root>/qemu` are
changed. The default root is `/var/lib/libvirt`.

Stop libvirtd and its guests, preview the change, then apply it:

```sh
sudo ./scripts/libvirt-qemu-migrate.sh
sudo ./scripts/libvirt-qemu-migrate.sh --apply
```

The preview changes nothing and prints the rollback command. `--apply`
requires root. The script refuses symlinked or unsafe root paths. The
`LIBVIRT_QEMU_MIGRATE_*` variables override the root, owner and group for the
tests only.

## system-age-key.py

Creates or adopts the root-only Age identity used for system secrets. The
default key is `/persist/secrets/age/system.key`, owned by root with mode 0600.
The script creates missing directories below `/persist` with mode 0700, but it
refuses to create `/persist` itself or to write when `/persist` is not mounted.
The private key is never printed, copied into the Nix store or passed on a
command line; only the public recipient is reported.

### Move the current identity

To make the existing desktop identity root-only, adopt it:

```sh
sudo ./scripts/system-age-key.py --adopt-desktop
```

`--adopt PATH` selects a different source file, for example:

```sh
sudo ./scripts/system-age-key.py --adopt /path/to/age.txt
```

Pass an absolute path. If the shell passes a literal `~` or `$HOME`, for example
because it was quoted, the script stops and asks for the expanded path. The
source must be a regular file with no group or other permissions; run
`chmod 600` first if it was copied loosely. The recipient does not
change, so `.sops.yaml` and the ciphertext files need no edit and no
`sops updatekeys` run. The option is enabled by default, so adopt the identity
before the next rebuild. `push-to-dir.sh` checks the configured key before it
touches `/etc/nixos` and refuses a missing or wrongly-owned key. The activation
script also stops with a clear error when the key is missing or has the wrong
owner or mode. Rebuild, verify the services start, then remove the old desktop
copy. Keeping that copy leaves the key readable by the desktop identity.

The same copy can be done directly, but the script checks the source mode,
refuses symlinks and an existing target, and prints the recipient:

```sh
sudo install -D -m 600 -o root -g root \
  "$HOME/.config/sops/age/keys.txt" /persist/secrets/age/system.key
```

### Create a new identity

Without `--adopt`, the script generates a new identity. For a fresh
installation, add its recipient to the creation rules that cover system secrets
in the same step:

```sh
sudo ./scripts/system-age-key.py --generate --write-sops-config
```

`--write-sops-config` inserts the new `- "age1..."` entry only into matching
`creation_rules`, preserves the rest of the file and its owner and mode, and
writes a private `.sops.yaml.bak-<timestamp>` backup. `--sops-config PATH`
selects another config file. If no rule matches `system/secrets/*.yaml`, the
script stops and asks for a manual edit.

The option is enabled by default, so this has to happen before the next
rebuild. If existing ciphertext was encrypted to another identity, re-encrypt
it while that identity still decrypts:

```sh
for f in system/secrets/{secrets,soul,skill,proxy}.yaml; do
  sudo SOPS_AGE_KEY_FILE=<old-key> sops updatekeys "$f"
done
```

A fresh installation has no such ciphertext. Without `--write-sops-config`, add
the printed recipient manually. `--print-recipient` reports the recipient of an
existing key.

`--directory` and `--key-name` select another key location. `--allow-unprivileged`
exists for tests against a throwaway directory; do not use it for a real key.
Moving an identity does not revoke access obtained before the move; rotate
exposed credentials separately.
