{ pkgs, ... }:

let
  pname = "dsh-desktop";
  version = "2.0.20";

  # Upstream publishes a type-2 AppImage for Linux. The AppDir keeps the
  # Electron tree at its root (AppRun + dsh-desktop-next + resources) and adds
  # a hicolor 1024 icon under usr/share. Since 2.0.18 the JavaScript payload is
  # packed into resources/app.asar, with native addons (and anything the Host
  # child process must read) beside it in resources/app.asar.unpacked.
  src = ./local-apps/DSH-NEXT-${version}-next-x86_64.AppImage;

  # Single unpack, reused as the runtime tree plus the desktop entry and icons.
  # postExtract patches vendor bugs (see inline notes); `--replace-fail` is
  # deliberate so a future release bump fails loudly.
  appDir = pkgs.appimageTools.extract {
    inherit pname version src;

    postExtract = ''
      appRoot=$out/resources
      app=$appRoot/app

      # asar extract also folds app.asar.unpacked back into the tree, which is
      # what the patches below need; the repack at the end recreates the split.
      # It copies file bytes only, so the executable bits upstream ships
      # (landlock-run, pnpm's node-gyp shims, ripgrep, ...) are lost; record
      # them from the original mirror and re-apply below before repacking.
      find "$appRoot/app.asar.unpacked" -type f -perm -u+x -printf '%P\0' > "$TMPDIR/exec-files"
      ${pkgs.asar}/bin/asar extract "$appRoot/app.asar" "$app"
      while IFS= read -r -d "" execFile; do
        chmod +x "$app/$execFile"
      done < "$TMPDIR/exec-files"

      # The Linux/macOS build installs a native application menu (the
      # "DSH NEXT / Edit / View / Window" bar) that eats a strip of the window.
      # It is set twice: once at boot and again from NativeDesktop.refresh(),
      # which the tray calls on every state change and would immediately put it
      # back. Neutralise both. To keep it reachable via Alt instead, drop this
      # patch and add `autoHideMenuBar: true` to the primary BrowserWindow.
      substituteInPlace "$app/lib/main.js" \
        --replace-fail \
        'Menu.setApplicationMenu(process.platform === "win32" ? null : Menu.buildFromTemplate([' \
        'Menu.setApplicationMenu(true ? null : Menu.buildFromTemplate(['
      substituteInPlace "$app/lib/main.js" \
        --replace-fail \
        'if (process.platform !== "win32") Menu.setApplicationMenu(Menu.buildFromTemplate([' \
        'if (false) Menu.setApplicationMenu(Menu.buildFromTemplate(['

      # Attachment uploads are fixed upstream since 2.0.18: web-document.js
      # injects __DSH_FILE_UPLOAD__ into APP_BOOT_SCRIPT itself, so the page
      # carrier no longer has to be added here.

      # The dshmarket git-source allowBuilds workarounds that used to live here
      # are gone: dshmarket >= 1.66 (bundled) derives both the stable
      # `name@git+https://…` and the commit-pinned codeload key itself
      # (dsh-market#267/#285/#637) and reads the exact key pnpm prints in its
      # refuse-to-prepare error (#698). Re-applying the old patches would only
      # corrupt parsePrepareNotAllowed()/routes.js.

      # The inner `pnpm install` pnpm runs for a git dependency falls back to
      # npm and auto-installs host-provided peers (404). Force pnpm and disable
      # autoInstallPeers, which the profile's own setting does not reach there.
      # pnpm 11.28.5 (bundled since 2.0.20) still has both shapes, but its
      # prepare-package block is indented with two spaces now.
      substituteInPlace "$app/node_modules/pnpm/dist/pnpm.mjs" \
        --replace-fail \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "npm";' \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "pnpm";'

      substituteInPlace "$app/node_modules/pnpm/dist/pnpm.mjs" \
        --replace-fail \
        '  manifest.scripts[installScriptName] = `''${pm2} install`;' \
        '  manifest.scripts[installScriptName] = `''${pm2} install''${pm2 === "pnpm" ? " --config.auto-install-peers=false" : ""}`;'

      # Since 2.0.20 Linux runs sharp on the WebAssembly build
      # (@img/sharp-wasm32) instead of native libvips, which is what removed the
      # Electron GLib symbol clash that used to need a post-link rename here.
      # Fail loudly if upstream ever flips back to the native package, so the
      # workaround cannot go missing silently.
      if [ ! -d "$app/node_modules/@img/sharp-wasm32" ]; then
        echo "dsh-desktop: sharp is no longer the WebAssembly build; revisit the removed glib rename workaround" >&2
        exit 1
      fi

      # Repack. node_modules/assets/build/scripts stay unpacked: the Host runs
      # in a child process that cannot read app.asar, and nativeImage needs real
      # files. asar accepts one --unpack-dir expression, hence the brace list.
      rm -rf "$appRoot/app.asar" "$appRoot/app.asar.unpacked"
      ${pkgs.asar}/bin/asar pack "$app" "$appRoot/app.asar" \
        --unpack-dir "{node_modules,assets,build,scripts}"
      rm -rf "$app"

      # 2.0.20's local sandbox spawns landlock-run as a real executable. If the
      # repack leaves it inside app.asar the spawn fails and every sandbox probe
      # reports the launcher unusable, so keep an explicit check on the mirror.
      landlockRun="$appRoot/app.asar.unpacked/node_modules/@deepseek-ai/node-addon-system-linux-x64/bin/landlock-run"
      if [ ! -x "$landlockRun" ]; then
        echo "dsh-desktop: landlock-run missing or not executable after repack: $landlockRun" >&2
        exit 1
      fi

      # Decode a real image through the unpacked WebAssembly sharp, so a repack
      # that hides the .wasm payload fails here instead of at runtime.
      ${pkgs.nodejs}/bin/node -e '
        const sharp = require(process.argv[1]);
        sharp({ create: { width: 8, height: 8, channels: 3, background: { r: 255, g: 0, b: 0 } } })
          .resize(4, 4).raw().toBuffer().then((buffer) => {
            if (buffer.length !== 48) throw new Error("unexpected byte length " + buffer.length);
          }).catch((error) => { console.error(error); process.exit(1); });
      ' "$appRoot/app.asar.unpacked/node_modules/sharp"

      # The AppImage ships a single 1024x1024 16-bit icon and no other hicolor
      # sizes; derive the standard set (forced 8-bit, as most icon caches
      # expect) under the desktop entry's icon name.
      for size in 1024 512 256 128 64 48 32 16; do
        mkdir -p "$out/usr/share/icons/hicolor/''${size}x''${size}/apps"
        ${pkgs.imagemagick}/bin/magick \
          "$out/usr/share/icons/hicolor/1024x1024/apps/dsh-desktop-next.png" \
          -resize "''${size}x''${size}" -strip -depth 8 \
          "$out/usr/share/icons/hicolor/''${size}x''${size}/apps/${pname}.png"
      done
    '';
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

  # The upstream AppRun probes user namespaces (unshare -Ur) and only then
  # drops the Chromium sandbox; the launcher replicates that because it calls
  # the binary directly (bypassing AppRun). Flags live here (not in Exec) so a
  # bare `dsh-desktop` invocation gets Wayland/IME handling too.
  launcher = pkgs.writeShellScript "dsh-desktop-launch" ''
    sandboxArgs=()
    if ! ${pkgs.util-linux}/bin/unshare -Ur true 2>/dev/null; then
      sandboxArgs=(--no-sandbox)
    fi

    exec ${appDir}/dsh-desktop-next \
      "''${sandboxArgs[@]}" \
      --class=${pname} \
      --ozone-platform-hint=auto \
      --enable-wayland-ime=true \
      "$@"
  '';

in
# No nixpkgs fallback; a missing local AppImage is a hard error.
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
    cp -r ${appDir}/usr/share/icons/. $out/share/icons/

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

