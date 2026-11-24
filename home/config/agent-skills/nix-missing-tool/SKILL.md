---
name: nix-missing-tool
description: Use when a command, library, or build dependency is missing on NixOS, or when maintaining a repository's Nix development environment.
---

# Provide missing Nix tools

Identify the target project and the scope of the dependency. Look up the package and rerun the command that failed. Use Nix for system tools and follow the project's existing workflow for Python virtual environments and language dependencies managed by tools such as npm, pnpm, or uv.

## Choose an environment

| Need | Approach |
| --- | --- |
| The target project has a Nix environment | Read the environment definition and README. Reuse the relevant development shell, package, or app output. |
| A few packages, temporary diagnostics, or a one-off script | Use `nix-shell -p` or `nix shell`. |
| Several coordinated dependencies or environment variables, common project commands, or an app entry point | Create a Flake when the project needs it or the user requests or strongly prefers one. |
| A general-purpose tool the user wants to keep installed | Add it to the existing Home Manager or NixOS package list. |

For repository environment definitions, see [Project Flake workflow](references/project-flakes.md). In a new Flake, use `nixos-unstable` for nixpkgs and pin the revision in `flake.lock`. Follow the input conventions of existing projects.

## Find the package

1. Run `command -v` to check the PATH. If the project defines an environment, check there too.
2. Use the NixOS MCP package query when available. Otherwise use `nix search nixpkgs <keyword>`.
3. A package attribute can differ from its command, as with `ripgrep` and `rg`. If you only have a filename or library name, use the existing nix-index database to find its package.

   ```bash
   nix-locate -w 'bin/rg'
   ```

If the index is missing, try a package search first. Generate or use a prebuilt index when a file-level lookup is needed. The `nix-index` package provides `nix-locate`.

## Run a tool temporarily

The examples use ripgrep. Substitute the package and command you need.

```bash
command -v rg
nix-shell -p ripgrep --run 'rg --version'
nix shell nixpkgs#ripgrep -c rg --version
nix run nixpkgs#ripgrep -- --version
```

- Put related tools in the same environment. For example, use `nix-shell -p cmake ninja pkg-config --run 'cmake --version'` and replace the command with the project's actual operation.
- Use `--run` or `-c` for automation. Use an interactive shell when the user needs to operate it directly.
- `nix run` requires a runnable package entry point. Use `nix shell` to add tools to the PATH.
- `nix-shell -p` resolves nixpkgs through `NIX_PATH`. `nixpkgs#` follows the registry. Check the source when a lookup disagrees with the local version. Reuse project inputs or specify a revision when pinning is required.

When the tool is available, rerun the original build, test, or diagnostic command to verify that its dependency is satisfied.

## Diagnose failures

| Symptom | Next check |
| --- | --- |
| `attribute … not found` | Verify the attribute path and nixpkgs revision. Look up the package by the command it provides. |
| The command is still missing inside the shell | Identify whether the main command or a subprocess failed. Make the missing tool available in the same environment. |
| Missing header, linker library, or `.so` | Use [Build and runtime dependencies](references/native-dependencies.md) to distinguish build inputs from runtime requirements. |
| `No such file or directory` for an existing file | Check the script interpreter or ELF loader. |
| Download or daemon access fails | Inspect the network, cache, or permission error. Use the client's authorization mechanism for restricted operations. Use `--offline` when the inputs and substitutes are available locally. |
| A temporary tool would build many dependencies | Inspect the build plan, platform, and available substitutes with `nix build --dry-run`. The `--offline` flag can still allow builds from source. |

Keep temporary dependencies outside the user profile. Change declarative configuration when the task calls for a persistent installation. Keep input updates, system activation, and store cleanup scoped to the user's request.
