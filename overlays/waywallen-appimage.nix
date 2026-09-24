{ pkgs, ... }:

let
  pname = "waywallen";
  version = "0.3.9";
  src = ./local-apps/waywallen-${version}-x86_64.AppImage;

  # Single extraction, reused for the runtime AppDir plus the desktop entry,
  # icon and metainfo.
  appDir = pkgs.appimageTools.extract { inherit pname version src; };

in
# Single source of truth for the local AppImage version. There is no nixpkgs
# package to fall back to, so a missing file is a hard error instead of a
# silently missing package. See overlays/local-apps/README.md for the filename.
if !builtins.pathExists src then
  throw "waywallen: ${toString src} is missing; see overlays/local-apps/README.md"
else
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

