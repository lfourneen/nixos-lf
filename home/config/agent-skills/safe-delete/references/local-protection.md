# Local deletion protections

In this reference, `~` refers to the current user's home directory. The path tables describe the default DSH launcher policy on this machine. File permissions, immutable flags, and client sandboxes can impose further restrictions.

## DSH writable paths

| Purpose | Paths |
| --- | --- |
| Projects and downloads | `~/Projects`, `~/Downloads` |
| Application state and caches | `~/.cache`, `~/.config`, `~/.local`, `~/.dsh` |
| JavaScript | `~/.npm`, `~/.bun`, `~/.yarn` |
| Go | `~/go` |
| Rust | `~/.cargo`, `~/.rustup` |
| JVM | `~/.gradle`, `~/.m2` |
| .NET | `~/.nuget`, `~/.dotnet` |
| Haskell | `~/.cabal`, `~/.stack`, `~/.ghcup` |
| OCaml | `~/.opam` |
| Elixir | `~/.mix`, `~/.hex` |
| Ruby | `~/.gem`, `~/.bundle` |
| PHP | `~/.composer` |
| Conda | `~/.conda` |
| Development and deployment tools | `~/.kube`, `~/.docker`, `~/.terraform.d` |
| Android | `~/Android`, `~/.android` |
| Temporary files and runtime interfaces | `/tmp`, `/dev`, `/proc`, `/run/user/<uid>` |

Directories must exist when the launcher applies its policy. Access to an unlisted path is independent of the working directory and symlinks pointing to it.

## DSH write restrictions

Outside the writable paths, DSH allows reads and execution by default. Creation, modification, renaming, and deletion require a write grant.

| Path | Default policy |
| --- | --- |
| `~/Documents`, `~/Videos`, `~/Music`, `~/Pictures`, `~/Desktop` | Outside DSH's writable paths. |
| `~/.ssh`, `~/.aws`, `~/.codex`, `~/.agents` | Outside DSH's writable paths. |
| User-created paths such as `~/work`, `~/src`, and `~/tmp` | Require a grant even when used as working or temporary directories. |
| Projects under `/mnt` or `/media` | Check for an explicit launcher grant. |
| `/etc`, `/var`, `/nix/store` | Outside DSH's writable paths and subject to system permissions. |

Moving an item into the trash requires write access to its original directory. The credentials below remain protected by immutable flags even inside a writable directory such as `~/.config`.

The Landlock policy applies to DSH processes started by this launcher and their descendants. At launch, `DSH_NO_LANDLOCK=1` disables that layer and `DSH_EXTRA_RW` adds writable paths. Inspect the existing launch settings when troubleshooting. Keep the policy in place during deletion. Changing those environment variables inside a running process has no effect on the rules already applied.

Codex, OpenCode, and other programs launched independently follow their own writable paths and authorization rules. A child process launched under DSH inherits its Landlock policy regardless of the executable name.

## Immutable files

The local home guard sets the immutable flag on existing files that match these paths by default.

- `~/.ssh/id_*`
- `~/.ssh/config`
- `~/.ssh/authorized_keys`
- `~/.config/sops/age/keys.txt`

The flag applies independently of DSH and restricts modification, renaming, and deletion for all clients. The policy applies to configured targets individually. Newly created matching files pick up the flag the next time the rules run.

Use `lsattr -d -- "$target"` to check for `i`. The immutable flag on a directory restricts changes to its entries. Check individual files for their own flags.

`home-guard lock` and `home-guard unlock` apply to every configured target. All subcommands, including `status`, invoke sudo when run as a regular user. Use `lsattr` to inspect flags. Obtain the corresponding authorization before clearing protection.

## Trash behavior

Interactive Brush aliases `rm` to `trash-put`. Call `trash-put` explicitly when trashing an item.

The home trash usually lives at `${XDG_DATA_HOME:-$HOME/.local/share}/Trash`. The `files` directory holds trashed items and `info` holds recovery metadata. Other mounts may use their own trash directories. Use `trash-list` to check records.

## Read-only diagnostics

Set `target` to the verified absolute path and inspect the effective permissions.

```bash
ls -ld -- "$target"
id
lsattr -d -- "$target"
findmnt -T "$target"
```

`Permission denied` and `Operation not permitted` can have different causes. Check ACLs and the client's sandbox if needed. The sandbox's view of mounts can differ from the host's. Record facts that could not be confirmed and use the active client's permission mechanism.
