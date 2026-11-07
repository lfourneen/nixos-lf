# local-data — generated build inputs

This directory contains generated build inputs produced by scripts in
`scripts/`. The DNS blocklist snapshot is tracked so a Git-backed flake
evaluation includes the same input as the checkout. Other generated files
are ignored unless explicitly listed in `.gitignore`.

## What lives here

| File | Produced by | Used by |
| --- | --- | --- |
| `dns-blocklist.txt.xz` | `scripts/refresh-dns-blocklist.py` | `system/config/dns-blocklist.nix` (rendered into `dnsmasq` `address=/…/` lines) |

`dns-blocklist.txt.xz` is the merged, de-duplicated name list behind the DNS
blocklist: one lowercase name per line, combining every entry of the upstreams
recorded in the header of `system/config/dns-blocklist.nix`. Its size follows
those upstreams. Each configuration build uses the checked-in snapshot.

## Regenerating

From the repository root:

```sh
./scripts/refresh-dns-blocklist.py          # fetch upstreams, rewrite data + header
./scripts/refresh-dns-blocklist.py --check  # report drift, write nothing
```

The refresh is manual on purpose: nothing is downloaded at build time or at
run time, so a build always uses exactly the list that was reviewed and
committed. Re-run it when `--check` reports drift.

## Why the file must exist on the build host

`system/config/dns-blocklist.nix` reads this file. That is why
`scripts/push-to-dir.sh` copies `local-data/` into `/etc/nixos` along with
`home/`, `overlays/` and `system/`: `nixos-rebuild --flake /etc/nixos#lfour`
evaluates the flake from there, and a missing file fails the evaluation with a
pointer back to this README.

The supported workflow is therefore: run `scripts/push-to-dir.sh` from this
checkout, then `nixos-rebuild --flake /etc/nixos#lfour`. The script copies
`local-data/` along with the tree and refuses to start when the data file is
missing, so an incomplete tree cannot reach the evaluator.

Git-backed evaluation includes tracked files. After refreshing the snapshot,
stage the updated file together with its header before running flake checks
or committing. Both `nixos-rebuild --flake .#lfour` and the `.#checks` outputs
can then use the snapshot from this checkout.
