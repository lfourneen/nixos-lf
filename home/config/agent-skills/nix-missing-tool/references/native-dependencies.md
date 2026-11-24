# Build and runtime dependencies

Identify whether the error occurs during compilation, linking, or program startup.

## Missing headers or linker libraries

Place the compiler tools and libraries in the same development environment. This example uses zlib.

```bash
nix-shell -p pkg-config zlib --run \
  'cc main.c $(pkg-config --cflags --libs zlib) -o app'
```

The package name, pkg-config module name, and header name can differ. Look up the package and its outputs. In a derivation, use `nativeBuildInputs` and `buildInputs` according to the dependency's role. When cross-compiling, distinguish tools that run on the build machine from libraries for the resulting program.

## A prebuilt program fails to start

Use `file` to check the architecture and file type. Inspect a script's shebang. For ELF programs, use `patchelf --print-interpreter`, `patchelf --print-needed`, and `patchelf --print-rpath` to inspect the loader and dependencies.

This machine provides nix-ld and the system library directory `/run/current-system/sw/share/nix-ld/lib`. nix-ld supports foreign binaries using the system ELF loader path. Inspect the active environment.

```bash
printf 'NIX_LD=%s\nNIX_LD_LIBRARY_PATH=%s\n' "${NIX_LD:-}" "${NIX_LD_LIBRARY_PATH:-}"
ls -ld -- /run/current-system/sw/share/nix-ld/lib
```

Pass the absolute path from `patchelf --print-interpreter` to `ls -l -- <loader-path>`. If it exists and points to nix-ld, reuse that support. If a library is missing, extend `NIX_LD_LIBRARY_PATH` for the affected process. If the loader or system library directory is missing, record the system, architecture, and path before choosing a runtime environment.

Adapt this temporary expression to the missing libraries and save it as `/tmp/runtime-libs.nix`.

```nix
let pkgs = import <nixpkgs> { };
in pkgs.mkShell {
  NIX_LD = pkgs.stdenv.cc.bintools.dynamicLinker;
  NIX_LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.zlib ]
    + ":/run/current-system/sw/share/nix-ld/lib";
}
```

Run `nix-shell /tmp/runtime-libs.nix --run '/absolute/path/to/program'` from any working directory. Drop the system library entry if that path is absent and provide the libraries the program needs. The temporary expression can remain outside the project Flake.

Nix-packaged Python and Node use the store's loader and do not pick up `NIX_LD_LIBRARY_PATH` automatically. Prefer adding the relevant Nix package when these processes load an additional `.so`. If `LD_LIBRARY_PATH` is required, scope it to the affected process and include only the needed library directories.

When packaging a foreign program persistently, consider `autoPatchelfHook`, a wrapper, or an FHS environment. For temporary troubleshooting, keep the original binary and verify the library-path configuration first.

[nix-ld usage and loader behavior](https://github.com/nix-community/nix-ld#usage)
