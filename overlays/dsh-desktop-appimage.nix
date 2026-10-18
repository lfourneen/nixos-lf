{ pkgs, ... }:

let
  pname = "dsh-desktop";
  version = "2.0.19";

  # Upstream publishes a type-2 AppImage for Linux. The AppDir keeps the
  # Electron tree at its root (AppRun + dsh-desktop-next + resources) and adds
  # a hicolor 1024 icon under usr/share. Since 2.0.18 the JavaScript payload is
  # packed into resources/app.asar, with native addons (and anything the Host
  # child process must read) beside it in resources/app.asar.unpacked.
  src = ./local-apps/DSH-NEXT-${version}-next-x86_64.AppImage;

  # Rename glib symbols inside sharp's prebuilt libvips and its Node binding.
  #
  # libvips statically links glib and exports its g_* symbols. Electron links
  # the system glib and leaks it into the process space, so sharp.node resolves
  # g_malloc/g_object_ref/... to Electron's glib instead of the copy libvips was
  # built against; g_object_unref on a libvips GObject then aborts the process
  # (electron#46323, sharp#4525). @janhapke/sharp-electron fixes this by
  # renaming those symbols at link time, but that rebuild only exists for sharp
  # 0.35.4 while DSH bundles 0.35.5. Renaming post-link keeps the bundled
  # version and cannot drift out of sync with it.
  renameGlib = pkgs.writeText "dsh-desktop-rename-glib.py" ''
    import sys

    import lief

    path = sys.argv[1]
    binary = lief.parse(path)
    renamed = 0
    for symbol in binary.dynamic_symbols:
        if symbol.name.startswith("g_"):
            symbol.name = "vips_" + symbol.name
            renamed += 1
    if renamed == 0:
        raise SystemExit(f"dsh-desktop: no glib symbols to rename in {path}")
    binary.write(path)
  '';

  renamePython = pkgs.python3.withPackages (ps: [ ps.lief ]);

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
      ${pkgs.asar}/bin/asar extract "$appRoot/app.asar" "$app"

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
      substituteInPlace "$app/node_modules/pnpm/dist/pnpm.mjs" \
        --replace-fail \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "npm";' \
        '  const pm2 = (await preferredPM(gitRootDir))?.name ?? "pnpm";'

      substituteInPlace "$app/node_modules/pnpm/dist/pnpm.mjs" \
        --replace-fail \
        '    manifest.scripts[installScriptName] = `''${pm2} install`;' \
        '    manifest.scripts[installScriptName] = `''${pm2} install''${pm2 === "pnpm" ? " --config.auto-install-peers=false" : ""}`;'

      # Rename the glib symbols on both sides of sharp's native boundary.
      # Only the linux-x64 packages are touched; the arm64 copies are unused.
      #
      # LIEF can segfault after it has written the new file, so its exit status
      # is not trusted: the symbol table and a real decode below are the actual
      # checks.
      vipsLib=$(echo "$app"/node_modules/@img/sharp-libvips-linux-x64/lib/libvips-cpp.so.*)
      sharpNode=$(echo "$app"/node_modules/@img/sharp-linux-x64/lib/sharp-linux-x64-*.node)
      ${renamePython}/bin/python3 ${renameGlib} "$vipsLib" || true
      ${renamePython}/bin/python3 ${renameGlib} "$sharpNode" || true

      for native in "$vipsLib" "$sharpNode"; do
        # `grep -c` rather than `grep -q`: the builder runs with pipefail, and
        # `nm | grep -q` can report a spurious failure when grep exits early
        # and nm is killed by SIGPIPE.
        glib=$(${pkgs.binutils}/bin/nm -D "$native" | grep -cE '\bg_' || true)
        renamed=$(${pkgs.binutils}/bin/nm -D "$native" | grep -c 'vips_g_' || true)
        if [ "$glib" -ne 0 ]; then
          echo "dsh-desktop: glib symbols survived in $native" >&2
          exit 1
        fi
        if [ "$renamed" -eq 0 ]; then
          echo "dsh-desktop: renaming glib symbols failed for $native" >&2
          exit 1
        fi
      done

      # Load the patched addon and decode a real image, so a broken rename can
      # never reach the packaged output.
      testPng="$TMPDIR/dsh-desktop-sharp-test.png"
      ${pkgs.imagemagick}/bin/magick -size 8x8 xc:red "$testPng"
      TEST_SHARP="$app/node_modules/sharp" TEST_PNG="$testPng" \
        ${pkgs.nodejs}/bin/node -e '
          const sharp = require(process.env.TEST_SHARP);
          sharp(process.env.TEST_PNG).resize(4, 4).raw().toBuffer().then(
            (buffer) => { if (buffer.length !== 48) throw new Error("unexpected byte length " + buffer.length); },
          ).catch((error) => { console.error(error); process.exit(1); });
        '

      # Repack. node_modules/assets/build/scripts stay unpacked: the Host runs
      # in a child process that cannot read app.asar, and nativeImage needs real
      # files. asar accepts one --unpack-dir expression, hence the brace list.
      rm -rf "$appRoot/app.asar" "$appRoot/app.asar.unpacked"
      ${pkgs.asar}/bin/asar pack "$app" "$appRoot/app.asar" \
        --unpack-dir "{node_modules,assets,build,scripts}"
      rm -rf "$app"

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

