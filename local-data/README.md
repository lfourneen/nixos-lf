# local-data — generated inputs that stay off Git

Anything in this directory is build input, not source: it is produced by a
script in `scripts/`, it is large, and it changes whenever its upstream lists
do. The contents are git-ignored (only this README is tracked) so the
repository stays small and history does not carry a multi-megabyte blob per
refresh. The file still has to be present wherever the flake is evaluated —
that is the prerequisite described below.

## What lives here

| File | Produced by | Used by |
| --- | --- | --- |
| `dns-blocklist.txt.xz` | `scripts/refresh-dns-blocklist.py` | `system/config/dns-blocklist.nix` (rendered into `dnsmasq` `address=/…/` lines) |

`dns-blocklist.txt.xz` is the merged, de-duplicated name list behind the DNS
blocklist: one lowercase name per line, combining every entry of the upstreams
recorded in the header of `system/config/dns-blocklist.nix`. Its size follows
those upstreams, so it is not pinned here.

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

A *pure* evaluation straight from the Git checkout cannot see the file (Nix
copies tracked files only). For a one-off build from here, evaluate the
checkout as a plain path instead: `nixos-rebuild switch --flake
path:/home/lfour/Projects/Nix/nixos-lf#lfour`. The flake checks in `.#checks`
do not read this file, so they keep working in the checkout either way.
