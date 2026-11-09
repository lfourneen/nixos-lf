{ pkgs, ... }:

let
  pname = "dsh-tui";
  version = "0.19.0";
  # Use the same native upstream host as environment.systemPackages.dsh
  # (currently supplied by the llm-agents input), including its dependencies.
  host = "${pkgs.dsh}/lib/node_modules/@deepseek-ai/dsh";
  releases = {
    x86_64-linux = {
      arch = "x64";
      hash = "sha256-x2UUwemAVDhi8eo6WYSTxCBIaUcYV6+BO2vwbhqibdw=";
    };
    aarch64-linux = {
      arch = "arm64";
      hash = "sha256-BqT/nWLJXzxm9KqemYxz3F3I3n9annSLUfdzj2wi2jY=";
    };
  };
  release = releases.${pkgs.stdenv.hostPlatform.system};
  src = pkgs.fetchurl {
    url = "https://github.com/ccch1mneyyy/dsh-TUI/releases/download/v${version}/dsh-tui-standalone-linux-${release.arch}.tar.gz";
    inherit (release) hash;
  };

  # Extract already-built JS/native modules from pkg's runtime.tar.gz asset.
  # Do not patchelf/strip the pkg executable: its appended payload uses fixed
  # offsets. The upstream executable also rewrites package.json and the user
  # patch at EVERY launch, so it cannot preserve installed plugins as-is.
  runtime = pkgs.stdenvNoCC.mkDerivation {
    pname = "${pname}-runtime";
    inherit version src;
    nativeBuildInputs = [
      pkgs.python3
      pkgs.nodejs_24
    ];
    unpackPhase = ''
      runHook preUnpack
      tar -xzf "$src"
      runHook postUnpack
    '';
    installPhase = ''
      runHook preInstall
      python3 ${./extract-runtime.py} dsh-tui runtime.tar.gz
      mkdir -p "$out"
      # Keep the TUI and its already-built dependencies, excluding the old
      # bundled host. Link ALL official packages from one native installation
      # so Cordis, its Loader and DSH services retain the same identities.
      # Extract before moving the old host aside: the archive hardlinks some
      # shared dependency files to paths inside its @deepseek-ai directory.
      tar -xzf runtime.tar.gz -C "$out"
      mv "$out/node_modules/@deepseek-ai" "$TMPDIR/bundled-deepseek-ai"
      mkdir -p "$out/node_modules/@deepseek-ai"
      for package in ${host}/node_modules/@deepseek-ai/*; do
        ln -s "$package" "$out/node_modules/@deepseek-ai/$(basename "$package")"
      done
      ln -s ${host} "$out/node_modules/@deepseek-ai/dsh"

      node - "$out" ${host} ${version} <<'JS'
      const { createRequire } = require('node:module');
      const { realpathSync } = require('node:fs');
      const req = createRequire(process.argv[2] + '/node_modules/@deepseek-harness-tui/dsh-tui/package.json');
      const hostReq = createRequire(process.argv[3] + '/package.json');
      const tui = req('./package.json');
      const host = hostReq('./package.json');
      if (tui.version !== process.argv[4]) throw new Error('unexpected TUI release');
      if (!req('semver').satisfies(host.version, tui.peerDependencies['@deepseek-ai/dsh-app-boot'], { includePrerelease: true })) {
        throw new Error('DSH ' + host.version + ' is outside the TUI supported range; update dsh-tui.nix');
      }
      if (realpathSync(req.resolve('@deepseek-ai/cordis')) !== realpathSync(hostReq.resolve('@deepseek-ai/cordis'))) {
        throw new Error('TUI and native DSH resolved different Cordis instances');
      }
      JS

      # /update must not replace the Nix-owned TUI or upgrade its host. Third
      # party plugin install/remove/enable/disable remain managed by DSH.
      substituteInPlace "$out/node_modules/@deepseek-harness-tui/dsh-tui/lib/types/update.js" \
        --replace-fail 'export async function updateTui(profile, plan, signal, options = {}) {' \
        'export async function updateTui(profile, plan, signal, options = {}) { console.error("dsh-tui: update overlays/dsh-tui/dsh-tui.nix / the llm-agents input and rebuild NixOS."); return { code: 1, tuiChanged: false, compatible: true };'
      runHook postInstall
    '';
    dontFixup = true;
  };

  tui = "${runtime}/node_modules/@deepseek-harness-tui/dsh-tui";
  launcher = pkgs.writeShellScript "dsh-tui-launch" ''
    set -euo pipefail
    export DSH_HOME="''${DSH_TUI_HOME:-''${XDG_DATA_HOME:-$HOME/.local/share}/dsh-tui}"
    export DSH_TUI_CLI_INSTALL_ANCHOR=${host}
    export DSH_TUI_UPDATE_ENGINE=0
    export DSH_TUI_UPDATE_LAUNCHER=0
    # Use upstream's supported dsh --profile entry. The native dsh wrapper
    # supplies --expose-internals required by its Cordis Loader; TUI's direct
    # host-entry child drops Node execArgv and cannot load that Loader.
    export DSH_TUI_HOST_ENTRY=0

    if [ "''${1:-}" = update ]; then
      echo 'dsh-tui: update overlays/dsh-tui/dsh-tui.nix / the llm-agents input and rebuild NixOS.' >&2
      exit 1
    fi

    # Serialize initialization/version changes without holding the lock while
    # the app runs. Only the two Nix-owned bundle links are refreshed.
    mkdir -p "$DSH_HOME"
    (
      flock -x 9
      ${pkgs.nodejs_24}/bin/node ${./init-profile.mjs} ${runtime}
    ) 9>"$DSH_HOME/.nix-profile.lock"

    exec ${pkgs.nodejs_24}/bin/node ${tui}/bin/dsh-tui.js "$@"
  '';

in
pkgs.buildFHSEnv {
  inherit pname version;
  # An FHS also covers native addons downloaded by plugins at runtime; merely
  # patching the release executable would leave those unusable on NixOS.
  targetPkgs =
    p: with p; [
      dsh
      nodejs_24
      pnpm
      git
      cacert
      bashInteractive
      coreutils
      util-linux
      ripgrep
      fd
      which
      xdg-utils
      stdenv.cc.cc.lib
      zlib
      glib
      openssl
      libsecret
    ];
  runScript = launcher;
  extraInstallCommands = ''
    ln -s dsh-tui "$out/bin/dst"
  '';
  passthru = { inherit runtime; };
  meta = {
    mainProgram = pname;
    description = "Terminal interface for DeepSeek Harness, repackaged from the portable release";
    homepage = "https://github.com/ccch1mneyyy/dsh-TUI";
    license = pkgs.lib.licenses.mit;
    platforms = builtins.attrNames releases;
    sourceProvenance = with pkgs.lib.sourceTypes; [ binaryNativeCode ];
  };
}
