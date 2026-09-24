{ pkgs, ... }:

let
  pname = "dsh-desktop";
  version = "2.0.13";
  src = ./local-apps/DSH-Desktop-${version}-x86_64.AppImage;

  # The bundled sharp 0.35.3 collides with Electron's glib (electron#46323 /
  # sharp#4525, segfaults image decode). @janhapke/sharp-electron is a drop-in
  # rebuild of that exact version with the symbols renamed at link time.
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

  # Single extraction, reused as the runtime AppDir plus the desktop entry and
  # icons. postExtract patches vendor bugs (see inline notes); `--replace-fail`
  # is deliberate so a future AppImage bump fails loudly.
  appDir = pkgs.appimageTools.extract {
    inherit pname version src;
    postExtract = ''
      # The runner guard `import.meta.main` is falsy under Electron, so only
      # force ELECTRON_RUN_AS_NODE (upstream does this on win32 only).
      substituteInPlace $out/resources/app/node_modules/@deepseek-ai/dsh-subprocess-local/lib/runner-launch-*.js \
        --replace-fail \
        'if (selection === WINDOWS_RUNNER_SELECTION && process.platform === "win32" && process.versions.electron !== void 0) {' \
        'if (process.versions.electron !== void 0) {'

      # dshmarket keys allowBuilds on the bare package name, which does not
      # match the exact `name@<codeload url>` pnpm wants for a git-hosted
      # plugin; rebuild that full key from pnpm's error line and pass it through.
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

      # The inner `pnpm install` pnpm runs for a git dependency falls back to
      # npm and auto-installs host-provided peers (404). Force pnpm and disable
      # autoInstallPeers, which the profile's own setting does not reach there.
      substituteInPlace $out/resources/app/node_modules/pnpm/dist/pnpm.mjs \
        --replace-fail \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "npm";' \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "pnpm";'

      substituteInPlace $out/resources/app/node_modules/pnpm/dist/pnpm.mjs \
        --replace-fail \
        '    manifest.scripts[installScriptName] = `''${pm2} install`;' \
        '    manifest.scripts[installScriptName] = `''${pm2} install''${pm2 === "pnpm" ? " --config.auto-install-peers=false" : ""}`;'

      # hicolor lists no 1024x1024 dir, so derive the standard icon sizes from
      # the AppImage's lone 1024x1024 source.
      for size in 512 256 128 64 48 32 16; do
        mkdir -p "$out/usr/share/icons/hicolor/''${size}x''${size}/apps"
        ${pkgs.imagemagick}/bin/magick \
          "$out/usr/share/icons/hicolor/1024x1024/apps/dsh-desktop.png" \
          -resize "''${size}x''${size}" \
          "$out/usr/share/icons/hicolor/''${size}x''${size}/apps/dsh-desktop.png"
      done

      # Swap the bundled sharp for the Electron-safe rebuild; the swap is
      # version-matched, so fail if a future AppImage bumps it.
      sharpDir=$out/resources/app/node_modules/sharp
      if [ "$(${pkgs.jq}/bin/jq -r .version "$sharpDir/package.json")" != "0.35.3" ]; then
        echo "dsh-desktop: bundled sharp is not 0.35.3; re-pin the sharp-electron swap" >&2
        exit 1
      fi
      # Remove the unpatched @img prebuilts (same SONAME) so the patched
      # libvips always wins.
      rm -rf "$sharpDir" \
        $out/resources/app/node_modules/@img/sharp-linux-x64 \
        $out/resources/app/node_modules/@img/sharp-libvips-linux-x64
      cp -r ${sharpElectron}/package/linux-x64/sharp "$sharpDir"
      chmod -R u+w "$sharpDir"
    '';
  };

in
# No nixpkgs fallback; a missing local AppImage is a hard error.
if !builtins.pathExists src then
  throw "dsh-desktop: ${toString src} is missing; see overlays/local-apps/README.md"
else
pkgs.appimageTools.wrapAppImage {
  inherit pname version;
  src = appDir;

  # `git` for git+https plugin installs; libdbusmenu-gtk3 is a NEEDED of
  # libappindicator3.so.1.
  extraPkgs = pkgs: with pkgs; [ git libdbusmenu-gtk3 ];

  # AppRun is not on PATH inside the FHS env, so repoint Exec at the wrapper.
  extraInstallCommands = ''
    install -m 444 -D ${appDir}/dsh-desktop.desktop \
      $out/share/applications/dsh-desktop.desktop
    substituteInPlace $out/share/applications/dsh-desktop.desktop \
      --replace-fail 'Exec=AppRun' \
        'Exec=${pname} --ozone-platform-hint=auto --enable-wayland-ime=true'

    # Copy into the existing hicolor tree (cp -r <dir> $out/share/ would nest).
    mkdir -p $out/share/icons
    cp -r ${appDir}/usr/share/icons/. $out/share/icons/
  '';

  # Keep the bundled Electron 43.3.0 (native modules are ABI 148) and the
  # namespace sandbox (chrome-sandbox is 0755; AppRun adds --no-sandbox only if
  # its probe fails).
  meta = {
    mainProgram = pname;
    description = "DSH Desktop: an Electron shell composed as a DeepSeek Harness Cordis plugin";
    homepage = "https://github.com/anywhere-labs/deepseek-harness-desktop";
    license = pkgs.lib.licenses.mit;
    platforms = pkgs.lib.platforms.linux;
  };
}

