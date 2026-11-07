{ pkgs, ... }:

let
  pname = "bilibili";
  version = "1.20.0";
  # Upstream tags the release v1.20.0-2 while the asset and version stay 1.20.0.
  src = pkgs.fetchurl {
    url = "https://github.com/msojocs/bilibili-linux/releases/download/v1.20.0-2/bilibili-${version}-x86_64.AppImage";
    hash = "sha256-FsACPr7MpS7gKUjSR6kC5lZi4VbQxF1QIrj8KytivLM=";
  };
  appimageContents = pkgs.appimageTools.extract { inherit pname version src; };

in
pkgs.appimageTools.wrapType2 {
  inherit pname version src;
  extraPkgs = pkgs: with pkgs; [
     bash
     gtk3
     nss
     nspr
     alsa-lib
     cups
     dbus
     at-spi2-atk
     libGL
     fcitx5-gtk
     xwayland
     xwayland-satellite
     libX11
     libXrandr
     libXcursor
     libXi
     libXcomposite
     libXdamage
     libXfixes
     libXScrnSaver
     libXtst
     libXext
     libglvnd
     libGL
     libGLU
     libGLX
     egl-wayland
     egl-wayland2
     vulkan-tools
     vulkan-loader
     vulkan-headers
   ];

  extraInstallCommands = ''
    for desktop in ${appimageContents}/*.desktop; do
      if [ -f "$desktop" ]; then
        desktopname=$(basename "$desktop")
        install -m 444 -D "$desktop" -t $out/share/applications

        # Change Exec=AppRun to Exec=bilibili
        substituteInPlace $out/share/applications/$desktopname \
          --replace-fail 'Exec=AppRun' 'Exec=bash -lc "exec ${pname} --no-sandbox"'
      fi
    done

    # Native Wayland + GPU is the default; pass --ozone-platform=x11 to
    # fall back to XWayland if the Electron build misbehaves.
    orig=$(readlink -f $out/bin/${pname})
    rm $out/bin/${pname}
    printf '%s\n' \
      '#!${pkgs.runtimeShell}' \
      "orig=\"$orig\"" \
      'for arg in "$@"; do' \
      '  case "$arg" in' \
      '    --ozone-platform|--ozone-platform=*) exec "$orig" "$@" ;;' \
      '  esac' \
      'done' \
      'exec "$orig" "$@" --ozone-platform=wayland' \
      > $out/bin/${pname}
    chmod +x $out/bin/${pname}

    # Copy icons
    cp -r ${appimageContents}/usr/share/icons $out/share 2>/dev/null || true
    cp -r ${appimageContents}/usr/share/pixmaps $out/share 2>/dev/null || true
    [ -f ${appimageContents}/.DirIcon ] && cp ${appimageContents}/.DirIcon $out/share/icons/hicolor/256x256/apps/${pname}.png || true
  '';

  meta = {
    mainProgram = pname;
    description = "Bilibili for Linux";
    homepage = "https://github.com/msojocs/bilibili-linux";
    platforms = pkgs.lib.platforms.linux;
  };
}

