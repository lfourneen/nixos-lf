{ pkgs, ... }:

let
  pname = "openidea";
  version = "0.1.38";

  # Tauri (WebKitGTK 4.1) AppImage. linuxdeploy strips the sysroot core, which
  # the FHS environment below supplies again.
  src = pkgs.fetchurl {
    url = "https://github.com/l0ng-ai/openidea/releases/download/v${version}/OpenIDEA_${version}_amd64.AppImage";
    hash = "sha256-rgPIMjoDBEBEG6I5e9MUzHAZhT8cwXqaU8Ci8egZZeE=";
  };

  appDir = pkgs.appimageTools.extract { inherit pname version src; };

in
pkgs.appimageTools.wrapAppImage {
  inherit pname version;
  src = appDir;

  extraPkgs = pkgs: with pkgs; [
    basedpyright

    # WebKit sandbox helpers; Debian ships them for libwebkit2gtk-4.1-0.
    # Opt-out if the host forbids nested user namespaces:
    # WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1
    bubblewrap
    xdg-dbus-proxy

    marksman
    nil
    nixd
    
    # The IDE downloads self-contained .NET tools (marksman, netcoredbg) and
    # runs them as children; they dlopen libicu, which NixOS exposes no loader
    # path for, so .NET fails fast with a misleading trace.
    icu
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
  '';

  # Upstream entry already reads Exec=openidea / StartupWMClass=openidea.
  extraInstallCommands = ''
    install -m 444 -D ${appDir}/usr/share/applications/OpenIDEA.desktop \
      $out/share/applications/OpenIDEA.desktop

    mkdir -p $out/share/icons
    cp -r ${appDir}/usr/share/icons/. $out/share/icons/
  '';

  meta = {
    mainProgram = pname;
    description = "An open-source IDE inspired by IntelliJ IDEA";
    homepage = "https://github.com/l0ng-ai/openidea";
    license = pkgs.lib.licenses.asl20;
    platforms = pkgs.lib.platforms.linux;
  };
}
