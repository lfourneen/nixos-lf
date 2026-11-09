{ runCommand, stdenv, coreutils, patchelf, repairTools }:

runCommand "openidea-repair-tools-check" {
  nativeBuildInputs = [ stdenv.cc coreutils patchelf repairTools ];
} ''
  mkdir -p "tools/path with spaces" outside
  printf 'int main(void) { return 0; }\n' > main.c
  cc main.c -o original
  cp original "tools/path with spaces/gopls"
  patchelf --set-interpreter /nix/store/collected-glibc/lib/ld-linux-x86-64.so.2 \
    "tools/path with spaces/gopls"

  # Valid loaders, shell launchers, non-executables and symlink targets stay intact.
  cp original tools/valid
  cp "tools/path with spaces/gopls" tools/non-executable
  chmod -x tools/non-executable
  cp "tools/path with spaces/gopls" outside/server
  ln -s "$PWD/outside/server" tools/linked
  printf '#!/bin/sh\nexit 0\n' > tools/script
  chmod +x tools/script
  cp -r tools before

  openidea-repair-tools "$PWD/tools" "$PWD/log/nix-tools.log"
  test "$(patchelf --print-interpreter "tools/path with spaces/gopls")" \
    = /lib64/ld-linux-x86-64.so.2
  test -x "tools/path with spaces/gopls"
  ${stdenv.cc.bintools.dynamicLinker} "tools/path with spaces/gopls"
  cmp tools/valid before/valid
  cmp tools/non-executable before/non-executable
  cmp outside/server before/non-executable
  cmp tools/script before/script
  test -L tools/linked
  test "$(wc -l < log/nix-tools.log)" -eq 1

  # Repeated launches do not rewrite repaired files or produce duplicate logs.
  cp "tools/path with spaces/gopls" repaired
  openidea-repair-tools "$PWD/tools" "$PWD/log/nix-tools.log"
  cmp "tools/path with spaces/gopls" repaired
  test "$(wc -l < log/nix-tools.log)" -eq 1
  openidea-repair-tools "$PWD/missing" "$PWD/log/nix-tools.log"
  touch "$out"
''
