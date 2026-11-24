# Project Flake workflow

Reuse or extend the target project's environment. Create a Flake when the criteria in the skill apply.

Find the absolute project path first. For Git projects, use `git -C "$project_root" rev-parse --show-toplevel`. For other projects, use the location given for the task. In `.#name`, the dot refers to the project root. When invoking Nix from elsewhere, use the absolute path.

```bash
project_root='/absolute/path/to/project'
nix develop "$project_root" --command project-command
nix build "$project_root#name"
nix run "$project_root#name" -- argument
```

Nix leaves the working directory unchanged. If builds or tests use relative paths, enter the project directory first or pass explicit source and output paths.

## Reuse an existing environment

Read `flake.nix`, related `.nix` files, `flake.lock`, the README, and CI to find the output names, target system, and project commands. Use `nix-shell` for projects that define `shell.nix`. If the only entry is `default.nix`, establish whether it defines a package, a development environment, or another value.

| Output | Invocation |
| --- | --- |
| `devShells.<system>.default` | `nix develop --command project-command` |
| `devShells.<system>.<name>` | `nix develop .#name --command project-command` |
| `packages.<system>.<name>` | `nix build .#name` |
| `apps.<system>.<name>` | `nix run .#name -- argument` |
| `checks.<system>` | `nix flake check` |

Use `--no-update-lock-file` to fail if the existing lock must change. The `--no-write-lock-file` flag only suppresses writes and still permits recalculation in memory. If the lock is missing or conflicts with the definitions, resolve it within the task's scope and inspect the diff.

Use a temporary shell for diagnostic tools and add actual build requirements to the existing environment. Match Python libraries to the interpreter, for example with `python3.withPackages`. Keep language dependencies under the project's existing lockfiles and build workflow.

## Define a development environment

This example is for a Linux C/C++ project. Adapt the systems and packages to the project. Select `nixos-unstable` and pin the resolved revision in the lock file.

```nix
{
  description = "Project development environment";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
    in {
      devShells = nixpkgs.lib.genAttrs systems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            packages = with pkgs; [ cmake ninja pkg-config ];
            PROJECT_MODE = "development";
          };
        });
    };
}
```

The default `mkShell` stdenv supplies a C/C++ compiler. Declare the remaining dependencies and environment variables in the shell. Inject secrets at runtime and keep them out of Nix expressions and the store. Preserve an existing project structure such as flake-parts.

## Provide a script entry point

Use the development shell to run project commands. For a `nix run` entry point, declare runtime dependencies and an app output. The example assumes `pkgs` and `system` are already in scope.

```nix
let
  runner = pkgs.writeShellApplication {
    name = "project-check";
    runtimeInputs = with pkgs; [ bash cmake ninja pkg-config stdenv.cc ];
    text = ''exec bash ${./scripts/check.sh} "$@"'';
  };
in {
  packages.${system}.check = runner;
  apps.${system}.check = {
    type = "app";
    program = "${runner}/bin/project-check";
  };
}
```

Replace `./scripts/check.sh` with the actual project script. Nix copies the interpolated path into the store at evaluation. The wrapper then finds the script regardless of the working directory.

Package any adjacent resources the script needs and pass their store paths. Accept the project directory, input files, and writable output paths as arguments. Write build outputs outside the read-only store. App outputs get their defaults from the wrapper and external configuration at runtime. Development-shell variables remain scoped to that shell.

## Manage the lock and source files

```bash
nix flake lock                    # Create the lock or add missing inputs
nix flake update nixpkgs          # Update the named input
git diff -- flake.nix flake.lock
```

`nix flake lock` preserves existing pins that are still valid. Changed input definitions can cause related entries to be recalculated. When upgrading an existing project, update only the inputs in scope and inspect the diff. Deliver `flake.nix`, `flake.lock`, and referenced files together for new projects.

Git Flake sources include tracked files. If a working-tree file is absent from the store source, inspect `git status --short`, `git ls-files -- <path>`, and `git check-ignore -- <path>`. Adding new source files to the index makes them visible to Flake evaluation before a commit. Preserve the user's staged changes. To verify untracked files, you can use `path:.` explicitly after checking for unrelated or sensitive files in the source.

## Verify the result

Run `nix flake check --no-build` to check evaluation, then run the relevant project commands. Build or test through a changed development environment. Exercise arguments and working-directory handling for a new app. Use `nix build` to verify a changed package in the build sandbox. Report the platforms actually tested.

Use the same lockfile and project commands in CI. Reuse `use flake` when the project uses direnv. Inspect `.envrc` before authorizing its execution.

[Nix development environments](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-develop) · [Lockfile updates](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake-update) · [Flake source rules](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake)
