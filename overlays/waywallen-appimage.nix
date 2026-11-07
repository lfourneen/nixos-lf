{ pkgs, ... }:

let
  pname = "waywallen";
  version = "0.5.0";
  src = pkgs.fetchurl {
    url = "https://github.com/waywallen/waywallen/releases/download/v${version}/waywallen-${version}-x86_64.AppImage";
    hash = "sha256-5iFsX//HBWEisQXldFlQye53cQ4pSmO2h8Szhx4ZAIM=";
  };

  # Single extraction, reused for the runtime AppDir plus the desktop entry,
  # icon and metainfo.
  appDir = pkgs.appimageTools.extract { inherit pname version src; };

in
pkgs.appimageTools.wrapAppImage {
  inherit pname version;
  src = appDir;

  # The AppImage omits the GPU/display stack (it must match the running driver);
  # listed explicitly rather than relying on appimageTools' FHS multiPkgs.
  extraPkgs = pkgs: with pkgs; [
    wayland
    libdrm
    libgbm
    vulkan-loader
    libglvnd
    libGL
    egl-wayland
    libxkbcommon
    libxcb
    libX11
    libICE
    libSM
    dbus
    fontconfig
    freetype
    harfbuzz
    zlib
  ];

  # AppRun execs `usr/bin/waywallen`, matching the wrapper name, so Exec works
  # unchanged.
  extraInstallCommands = ''
    install -m 444 -D ${appDir}/usr/share/applications/org.waywallen.waywallen.desktop \
      $out/share/applications/org.waywallen.waywallen.desktop

    install -m 444 -D ${appDir}/usr/share/metainfo/org.waywallen.waywallen.metainfo.xml \
      $out/share/metainfo/org.waywallen.waywallen.metainfo.xml

    # Copy into the existing hicolor tree (cp -r <dir> $out/share/ would nest).
    mkdir -p $out/share/icons
    cp -r ${appDir}/usr/share/icons/. $out/share/icons/
  '';

  meta = {
    mainProgram = pname;
    description = "Wallpaper manager for Linux with DMA-BUF shared GPU rendering";
    homepage = "https://github.com/waywallen/waywallen";
    license = pkgs.lib.licenses.gpl3Plus;
    platforms = pkgs.lib.platforms.linux;
  };
}

