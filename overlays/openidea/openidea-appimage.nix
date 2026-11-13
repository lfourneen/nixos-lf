{ pkgs, ... }:

let
  pname = "openidea";
  version = "0.1.44";

  # Tauri (WebKitGTK 4.1) AppImage. linuxdeploy strips the sysroot core, which
  # the FHS environment below supplies again.
  src = pkgs.fetchurl {
    url = "https://github.com/l0ng-ai/openidea/releases/download/v${version}/OpenIDEA_${version}_amd64.AppImage";
    hash = "sha256-0cjca6zkUzBuH6hKnFZfRbaKu8I4KWYot+E5IY6VC08=";
  };

  appDir = pkgs.appimageTools.extract { inherit pname version src; };

  repairTools = pkgs.writeShellApplication {
    name = "openidea-repair-tools";
    runtimeInputs = with pkgs; [ coreutils findutils patchelf ];
    text = builtins.readFile ./repair-tools.sh;
  };

in
pkgs.appimageTools.wrapAppImage {
  inherit pname version;
  src = appDir;

  extraPkgs = pkgs: with pkgs; [
    # WebKit sandbox helpers; Debian ships them for libwebkit2gtk-4.1-0.
    # Opt-out if the host forbids nested user namespaces:
    # WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1
    bubblewrap
    xdg-dbus-proxy

    nodejs

    # The IDE downloads self-contained .NET tools (marksman, netcoredbg) and
    # runs them as children; they dlopen libicu, which NixOS exposes no loader
    # path for, so .NET fails fast with a misleading trace.
    icu
    stdenv.cc.cc.lib
  ];

  # The session injects NixOS' GIO modules (gvfs, glib-networking, dconf) built
  # against a newer glib than the bundled 2.72, so dconf fails to load and every
  # GSettings value falls back to its default (cursor theme, icons, ...). The
  # AppImage ships its own modules and sets GIO_MODULE_DIR, so drop the foreign
  # scan path.
  #
  # Bundled libXcursor also only searches ~/.icons:/usr/share/icons:/usr/share/pixmaps,
  # while nixpkgs' patched one adds ~/.local/share/icons where themes are kept.
  profile = ''
    unset GIO_EXTRA_MODULES
    export XCURSOR_PATH="''${XDG_DATA_HOME:-$HOME/.local/share}/icons:$HOME/.icons''${XCURSOR_PATH:+:$XCURSOR_PATH}:/usr/share/icons:/usr/share/pixmaps"

    # `go install` with Nix's Go may embed a store loader without creating a
    # GC root. After collection the executable exists but execve returns ENOENT.
    # Use this FHS environment's stable loader for affected managed tools.
    if ! ${repairTools}/bin/openidea-repair-tools; then
      echo "OpenIDEA: could not repair managed tools; see logs/nix-tools.log" >&2
    fi
  '';

  # Upstream entry already reads Exec=openidea / StartupWMClass=openidea.
  extraInstallCommands = ''
    install -m 444 -D ${appDir}/usr/share/applications/OpenIDEA.desktop \
      $out/share/applications/OpenIDEA.desktop

    mkdir -p $out/share/icons
    cp -r ${appDir}/usr/share/icons/. $out/share/icons/

    ln -s ${repairTools}/bin/openidea-repair-tools $out/bin/openidea-repair-tools
  '';

  passthru.tests.repair-tools = pkgs.callPackage ./test-repair-tools.nix {
    inherit repairTools;
  };

  meta = {
    mainProgram = pname;
    description = "An open-source IDE inspired by IntelliJ IDEA";
    homepage = "https://github.com/l0ng-ai/openidea";
    license = pkgs.lib.licenses.asl20;
    platforms = pkgs.lib.platforms.linux;
  };
}
