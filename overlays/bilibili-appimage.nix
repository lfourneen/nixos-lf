{ pkgs, ... }:

let
  pname = "bilibili";
  version = "1.17.9";
  src = ./local-apps/bilibili-${version}-x86_64.AppImage;
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
   ];

  extraInstallCommands = ''
    for desktop in ${appimageContents}/*.desktop; do
      if [ -f "$desktop" ]; then
        desktopname=$(basename "$desktop")
        install -m 444 -D "$desktop" -t $out/share/applications

        # Change Exec=AppRun to Exec=bilibili
        substituteInPlace $out/share/applications/$desktopname \
          --replace-fail 'Exec=AppRun' 'Exec=bash -lc "exec ${pname}"' 
      fi
    done

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

