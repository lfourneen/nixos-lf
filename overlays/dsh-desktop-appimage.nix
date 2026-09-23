{ pkgs, ... }:

let
  pname = "dsh-desktop";
  version = "2.0.13";
  src = ./local-apps/DSH-Desktop-${version}-x86_64.AppImage;

  # The AppImage bundles sharp 0.35.3, whose prebuilt libvips statically links
  # its own glib and re-exports the g_* symbols. Electron's Linux binary links
  # the system glib, so both copies land in the DSH Host (the node.mojom
  # utility process) and glib's internal state is corrupted: the first image
  # decode segfaults the whole host (SIGSEGV in g_object_unref). Every remote
  # call after that fails with `client api: <endpoint> failed: Failed to fetch`,
  # including `subagents/list` and `subagents/prompt`. This is the known
  # electron/electron#46323 / lovell/sharp#4525 glib symbol collision.
  #
  # @janhapke/sharp-electron is a drop-in rebuild of exactly this sharp version
  # that hides and renames the colliding glib symbols at the linker level (no
  # env var or LD_PRELOAD fixes the stock build). `linux-x64/sharp` ships both
  # the patched addon and the patched libvips-cpp side by side.
  sharpElectronSrc = pkgs.fetchurl {
    url = "https://registry.npmjs.org/@janhapke/sharp-electron/-/sharp-electron-0.35.3-electron.1.tgz";
    hash = "sha512-ZDnFPMsKolANxS+RDjgxw6/jc6O0JfJ0msu6bgBdPCz6NFm4+K+cPurwXB+rdl5UY2/EQ5bzQ7ENYnRXtdprLQ==";
  };
  sharpElectron = pkgs.runCommand "sharp-electron-0.35.3-electron.1" {
    nativeBuildInputs = [ pkgs.libarchive ];
  } ''
    mkdir -p $out
    bsdtar -xf ${sharpElectronSrc} -C $out
  '';

  # Single extraction, used both as the runtime AppDir and as the source for the
  # desktop entry and icons.
  #
  # postExtract patches several vendor bugs. The first makes plugin
  # operations possible at all on Linux: `dsh-subprocess-local` launches its
  # runner as `<electron> <runner.js>`, and the runner's entry guard is
  # `if (import.meta.main)`. `import.meta.main` is a Node feature; in
  # Electron's GUI main process it is falsy, so the runner exits 0 without
  # consuming the launch request and the caller reports "subprocess scope
  # exited before its bootstrap consumed the launch request" (exit 127). The
  # package already forces ELECTRON_RUN_AS_NODE=1 for this reason, but only on
  # win32, so the Linux desktop app can never install, update, or roll back a
  # plugin. The rest fix the market's git-hosted build approval and the inner
  # `pnpm install` pnpm runs for such a dependency (below).
  #
  # `--replace-fail` is deliberate: if a future AppImage changes this line the
  # build fails loudly instead of silently shipping a broken market.
  appDir = pkgs.appimageTools.extract {
    inherit pname version src;
    postExtract = ''
      substituteInPlace $out/resources/app/node_modules/@deepseek-ai/dsh-subprocess-local/lib/runner-launch-*.js \
        --replace-fail \
        'if (selection === WINDOWS_RUNNER_SELECTION && process.platform === "win32" && process.versions.electron !== void 0) {' \
        'if (process.versions.electron !== void 0) {'

      # dshmarket's one-click "allow build scripts and retry" button derives
      # the allowBuilds key from the bare NAME pnpm reports. That is enough
      # for the plain postinstall-block case, but not for a git-hosted plugin
      # whose prepare script pnpm rejected in its FETCHER
      # (ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED): there pnpm matches only the
      # exact `name@<codeload tarball url>` key from its own hint, so the bare
      # name authorizes nothing. The route then finds no anchor (not in
      # node_modules, not in package.json, not in the curated catalog) and
      # answers "no installed packages given".
      #
      # The bundled pnpm 11.8.0 prints the fetched tarball URL in the same
      # error line, so the exact key can be rebuilt: keep it whole instead of
      # stripping the version, and let the route pass a full codeload key
      # straight through (setAllowBuilds already accepts that shape).
      substituteInPlace $out/resources/app/node_modules/dshmarket/lib/install.js \
        --replace-fail \
        '    return at > 0 ? raw.slice(0, at) : raw;' \
        '    const u = /Failed to prepare git-hosted package fetched from "([^"]+)"/.exec(text); return u === null ? (at > 0 ? raw.slice(0, at) : raw) : (at > 0 ? raw.slice(0, at) : raw) + "@" + u[1];'

      substituteInPlace $out/resources/app/node_modules/dshmarket/lib/routes.js \
        --replace-fail \
        '                    const stripVersion = (name) => {' \
        '                    const stripVersion = (name) => { if (/@(?:git\+https:\/\/|https:\/\/codeload\.github\.com\/)/.test(name)) return name;'

      substituteInPlace $out/resources/app/node_modules/dshmarket/lib/routes.js \
        --replace-fail \
        '                        .filter(name => PKG_RE.test(name));' \
        '                        .filter(name => PKG_RE.test(name) || /@(?:git\+https:\/\/|https:\/\/codeload\.github\.com\/)/.test(name));'

      substituteInPlace $out/resources/app/node_modules/dshmarket/lib/routes.js \
        --replace-fail \
        '                    for (const name of requested) {' \
        '                    for (const name of requested) { if (/@(?:git\+https:\/\/|https:\/\/codeload\.github\.com\/)/.test(name)) { packages.push(name); continue; }'

      # A git-hosted plugin with a prepare script makes pnpm run its own
      # `pnpm install` in the fetched repo BEFORE the prepare hook. Two things
      # about that inner install break dsh plugins whose peers are host-
      # provided (e.g. yjh051108/dsh-routing-suite, whose root declares
      # @deepseek-ai/dsh-tools / cordis / schemastery):
      #
      #  1. preferredPM falls back to npm when the repo ships no root lockfile,
      #     and npm (like pnpm) then tries to fetch the host peers from the
      #     registry — 404, inner install exits 1, and the whole operation
      #     dies with ERR_PNPM_PREPARE_PACKAGE. dsh is a pnpm ecosystem and
      #     bundles pnpm, so the fallback is pnpm.
      #  2. pnpm auto-installs peers by default (autoInstallPeers), which is
      #     exactly what 404s. dsh profiles already set autoInstallPeers:
      #     false; the inner install runs in a store temp dir and does not see
      #     the profile, so the flag is forced here. This only affects the
      #     `pnpm install` that pnpm itself runs for a git dependency.
      substituteInPlace $out/resources/app/node_modules/pnpm/dist/pnpm.mjs \
        --replace-fail \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "npm";' \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "pnpm";'

      substituteInPlace $out/resources/app/node_modules/pnpm/dist/pnpm.mjs \
        --replace-fail \
        '    manifest.scripts[installScriptName] = `''${pm2} install`;' \
        '    manifest.scripts[installScriptName] = `''${pm2} install''${pm2 === "pnpm" ? " --config.auto-install-peers=false" : ""}`;'

      # hicolor's index.theme (hicolor-icon-theme) lists no 1024x1024
      # directory, so the AppImage's lone 1024x1024 icon is invisible to every
      # theme-based lookup: Qt/GTK launchers and Noctalia's app grid show the
      # app without an icon even though the tray renders it from its own
      # pixmap. Derive the standard sizes from that source icon.
      for size in 512 256 128 64 48 32 16; do
        mkdir -p "$out/usr/share/icons/hicolor/''${size}x''${size}/apps"
        ${pkgs.imagemagick}/bin/magick \
          "$out/usr/share/icons/hicolor/1024x1024/apps/dsh-desktop.png" \
          -resize "''${size}x''${size}" \
          "$out/usr/share/icons/hicolor/''${size}x''${size}/apps/dsh-desktop.png"
      done

      # Swap the bundled sharp for the Electron-safe rebuild (see the
      # sharpElectron comment above for why). Fail loudly if a future AppImage
      # bumps sharp, because the swap is version-matched.
      sharpDir=$out/resources/app/node_modules/sharp
      if [ "$(${pkgs.jq}/bin/jq -r .version "$sharpDir/package.json")" != "0.35.3" ]; then
        echo "dsh-desktop: bundled sharp is not 0.35.3; re-pin the sharp-electron swap" >&2
        exit 1
      fi
      # The unpatched @img prebuilts carry a libvips-cpp with the same SONAME;
      # if anything loads them first the patched addon would bind to the wrong
      # glib again. Remove them so the co-located patched libvips always wins.
      rm -rf "$sharpDir" \
        $out/resources/app/node_modules/@img/sharp-linux-x64 \
        $out/resources/app/node_modules/@img/sharp-libvips-linux-x64
      cp -r ${sharpElectron}/package/linux-x64/sharp "$sharpDir"
      chmod -R u+w "$sharpDir"
    '';
  };

in
# Single source of truth for the local AppImage version. There is no nixpkgs
# package to fall back to, so a missing file is a hard error instead of a
# silently missing package. See overlays/local-apps/README.md for the filename.
if !builtins.pathExists src then
  throw "dsh-desktop: ${toString src} is missing; see overlays/local-apps/README.md"
else
pkgs.appimageTools.wrapAppImage {
  inherit pname version;
  src = appDir;

  # `git` for git+https plugin installs; libdbusmenu-gtk3 is a transitive
  # NEEDED of the AppDir's libappindicator3.so.1.
  extraPkgs = pkgs: with pkgs; [ git libdbusmenu-gtk3 ];

  # AppRun is not on PATH inside the FHS env, so repoint Exec at the wrapper.
  extraInstallCommands = ''
    install -m 444 -D ${appDir}/dsh-desktop.desktop \
      $out/share/applications/dsh-desktop.desktop
    substituteInPlace $out/share/applications/dsh-desktop.desktop \
      --replace-fail 'Exec=AppRun' \
        'Exec=${pname} --ozone-platform-hint=auto --enable-wayland-ime=true'

    # Copy into the existing hicolor tree; `cp -r <dir> $out/share/` would nest
    # it as $out/share/icons/icons.
    mkdir -p $out/share/icons
    cp -r ${appDir}/usr/share/icons/. $out/share/icons/
  '';

  # The bundled Electron 43.3.0 stays: native modules ship prebuilt for ABI 148
  # and the app's own pnpm shim re-execs that binary as Node. No `--no-sandbox`
  # either — AppDir chrome-sandbox is 0755, so the namespace sandbox is what
  # runs, and AppRun already adds the flag if its own probe fails.
  meta = {
    mainProgram = pname;
    description = "DSH Desktop: an Electron shell composed as a DeepSeek Harness Cordis plugin";
    homepage = "https://github.com/anywhere-labs/deepseek-harness-desktop";
    license = pkgs.lib.licenses.mit;
    platforms = pkgs.lib.platforms.linux;
  };
}

