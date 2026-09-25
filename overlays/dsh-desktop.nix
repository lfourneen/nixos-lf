{ pkgs, ... }:

let
  pname = "dsh-desktop";
  version = "2.0.14";

  # Upstream now publishes a plain Electron tarball for Linux (no AppImage).
  # The archive root is DSH-NEXT-<version>-next-linux-x64 and the executable is
  # dsh-desktop-next; no AppRun/desktop entry/icon sizes ship with it.
  src = ./local-apps/DSH-NEXT-${version}-next-linux-x64.tar.gz;

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

  # Single unpack, reused as the runtime tree plus the desktop entry and icons.
  # installPhase patches vendor bugs (see inline notes); `--replace-fail` is
  # deliberate so a future release bump fails loudly.
  app = pkgs.stdenvNoCC.mkDerivation {
    pname = "${pname}-unpacked";
    inherit version src;

    nativeBuildInputs = [
      pkgs.imagemagick
      pkgs.jq
    ];

    sourceRoot = "DSH-NEXT-${version}-next-linux-x64";
    dontConfigure = true;
    dontBuild = true;

    installPhase = ''
      runHook preInstall

      mkdir -p $out/lib/dsh-desktop
      cp -a . $out/lib/dsh-desktop

      appDir=$out/lib/dsh-desktop/resources/app

      # The Linux/macOS build installs a native application menu (the
      # "DSH NEXT / Edit / View / Window" bar) that eats a strip of the window.
      # It is set twice: once at boot and again from NativeDesktop.refresh(),
      # which the tray calls on every state change and would immediately put it
      # back. Neutralise both. To keep it reachable via Alt instead, drop this
      # patch and add `autoHideMenuBar: true` to the primary BrowserWindow.
      substituteInPlace $appDir/lib/main.js \
        --replace-fail \
        'Menu.setApplicationMenu(process.platform === "win32" ? null : Menu.buildFromTemplate([' \
        'Menu.setApplicationMenu(true ? null : Menu.buildFromTemplate(['
      substituteInPlace $appDir/lib/main.js \
        --replace-fail \
        'if (process.platform !== "win32") Menu.setApplicationMenu(Menu.buildFromTemplate([' \
        'if (false) Menu.setApplicationMenu(Menu.buildFromTemplate(['

      # dshmarket keys allowBuilds on the bare package name, which does not
      # match the exact `name@<codeload url>` pnpm wants for a git-hosted
      # plugin; rebuild that full key from pnpm's error line and pass it through.
      substituteInPlace $appDir/node_modules/dshmarket/lib/install.js \
        --replace-fail \
        '    return at > 0 ? raw.slice(0, at) : raw;' \
        '    const u = /Failed to prepare git-hosted package fetched from "([^"]+)"/.exec(text); return u === null ? (at > 0 ? raw.slice(0, at) : raw) : (at > 0 ? raw.slice(0, at) : raw) + "@" + u[1];'

      substituteInPlace $appDir/node_modules/dshmarket/lib/routes.js \
        --replace-fail \
        '                    const stripVersion = (name) => {' \
        '                    const stripVersion = (name) => { if (/@(?:git\+https:\/\/|https:\/\/codeload\.github\.com\/)/.test(name)) return name;'

      substituteInPlace $appDir/node_modules/dshmarket/lib/routes.js \
        --replace-fail \
        '                        .filter(name => PKG_RE.test(name));' \
        '                        .filter(name => PKG_RE.test(name) || /@(?:git\+https:\/\/|https:\/\/codeload\.github\.com\/)/.test(name));'

      substituteInPlace $appDir/node_modules/dshmarket/lib/routes.js \
        --replace-fail \
        '                    for (const name of requested) {' \
        '                    for (const name of requested) { if (/@(?:git\+https:\/\/|https:\/\/codeload\.github\.com\/)/.test(name)) { packages.push(name); continue; }'

      # The inner `pnpm install` pnpm runs for a git dependency falls back to
      # npm and auto-installs host-provided peers (404). Force pnpm and disable
      # autoInstallPeers, which the profile's own setting does not reach there.
      substituteInPlace $appDir/node_modules/pnpm/dist/pnpm.mjs \
        --replace-fail \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "npm";' \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "pnpm";'

      substituteInPlace $appDir/node_modules/pnpm/dist/pnpm.mjs \
        --replace-fail \
        '    manifest.scripts[installScriptName] = `''${pm2} install`;' \
        '    manifest.scripts[installScriptName] = `''${pm2} install''${pm2 === "pnpm" ? " --config.auto-install-peers=false" : ""}`;'

      # Swap the bundled sharp for the Electron-safe rebuild; the swap is
      # version-matched, so fail if a future release bumps it.
      sharpDir=$appDir/node_modules/sharp
      if [ "$(${pkgs.jq}/bin/jq -r .version "$sharpDir/package.json")" != "0.35.3" ]; then
        echo "dsh-desktop: bundled sharp is not 0.35.3; re-pin the sharp-electron swap" >&2
        exit 1
      fi
      # Remove the unpatched @img prebuilts (same SONAME) so the patched
      # libvips always wins.
      rm -rf "$sharpDir" \
        $appDir/node_modules/@img/sharp-linux-x64 \
        $appDir/node_modules/@img/sharp-libvips-linux-x64
      cp -r ${sharpElectron}/package/linux-x64/sharp "$sharpDir"
      chmod -R u+w "$sharpDir"

      # The tarball ships a single 1024x1024 16-bit icon and no hicolor tree;
      # derive the standard sizes (forced 8-bit, as most icon caches expect).
      for size in 512 256 128 64 48 32 16; do
        mkdir -p "$out/share/icons/hicolor/''${size}x''${size}/apps"
        ${pkgs.imagemagick}/bin/magick \
          "$appDir/build/app-icon.png" \
          -resize "''${size}x''${size}" -strip -depth 8 \
          "$out/share/icons/hicolor/''${size}x''${size}/apps/dsh-desktop.png"
      done

      runHook postInstall
    '';

    meta.sourceProvenance = with pkgs.lib.sourceTypes; [ binaryNativeCode ];
  };

  desktopItem = pkgs.makeDesktopItem {
    name = pname;
    desktopName = "DSH Desktop";
    comment = "DSH Desktop: an Electron shell composed as a DeepSeek Harness Cordis plugin";
    exec = "${pname} %U";
    icon = pname;
    startupWMClass = pname;
    categories = [ "Development" ];
    terminal = false;
  };

  # The upstream AppRun probes user namespaces and only then drops the Chromium
  # sandbox; replicate that so a kernel without unprivileged userns still
  # starts the app instead of aborting. Flags live here (not in Exec) so a bare
  # `dsh-desktop` invocation gets Wayland/IME handling too.
  launcher = pkgs.writeShellScript "dsh-desktop-launch" ''
    sandboxArgs=()
    if ! ${pkgs.util-linux}/bin/unshare -Ur true 2>/dev/null; then
      sandboxArgs=(--no-sandbox)
    fi

    exec ${app}/lib/dsh-desktop/dsh-desktop-next \
      "''${sandboxArgs[@]}" \
      --class=${pname} \
      --ozone-platform-hint=auto \
      --enable-wayland-ime=true \
      "$@"
  '';

in
# No nixpkgs fallback; a missing local tarball is a hard error.
if !builtins.pathExists src then
  throw "dsh-desktop: ${toString src} is missing; see overlays/local-apps/README.md"
else
# Prebuilt Electron trees are hostile to autoPatchelf: the runtime ships its own
# libffmpeg/libvulkan and dozens of native .node addons that resolve siblings
# through $ORIGIN RPATHs. An FHS supplies the standard library search paths
# instead, matching what appimageTools used for this package previously.
pkgs.buildFHSEnv {
  inherit pname version;

  targetPkgs = pkgs: with pkgs; [
    # Electron's own NEEDED set (mirrors nixpkgs' electron) plus the X11/GTK
    # stack the renderer dlopens.
    alsa-lib
    at-spi2-atk
    cairo
    cups
    dbus
    expat
    gdk-pixbuf
    glib
    gtk3
    nss
    nspr
    libdrm
    libgbm
    libxkbcommon
    libxshmfence
    libGL
    vulkan-loader
    libnotify
    pipewire
    libsecret
    libpulseaudio
    libx11
    libxcb
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxrandr
    libxinerama
    libxcursor
    libxrender
    libxi
    libxtst
    libxkbfile
    pango
    pciutils
    udev
    wayland
    fontconfig
    freetype
    harfbuzz
    zlib

    # Tray support: Electron dlopens libappindicator3, which NEEDs
    # libdbusmenu-gtk3.
    libappindicator-gtk3
    libdbusmenu-gtk3

    # Runtime helpers: `git` for git+https plugin installs, `nodejs` so the
    # market's one-click setup can still find npm/corepack (it otherwise only
    # sees the bundled Electron-as-node shim), and the usual desktop utilities.
    git
    nodejs
    util-linux
    xdg-utils
    xdg-user-dirs
    gsettings-desktop-schemas
    hicolor-icon-theme
    iana-etc
    krb5
    zenity
    which
    bashInteractive
  ];

  runScript = launcher;

  extraInstallCommands = ''
    install -d $out/share/icons
    cp -r ${app}/share/icons/. $out/share/icons/

    install -m 444 -D ${desktopItem}/share/applications/${pname}.desktop \
      $out/share/applications/${pname}.desktop
  '';

  meta = {
    mainProgram = pname;
    description = "DSH Desktop: an Electron shell composed as a DeepSeek Harness Cordis plugin";
    homepage = "https://github.com/anywhere-labs/deepseek-harness-desktop";
    license = pkgs.lib.licenses.mit;
    platforms = pkgs.lib.platforms.linux;
    sourceProvenance = with pkgs.lib.sourceTypes; [ binaryNativeCode ];
  };
}

