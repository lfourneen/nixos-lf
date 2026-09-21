{ pkgs, ... }:

let
  pname = "waywallen";
  version = "0.3.9";
  src = ./local-apps/waywallen-${version}-x86_64.AppImage;

  # Single extraction, reused for the runtime AppDir plus the desktop entry,
  # icon and metainfo. The AppImage bundles Qt 6, FFmpeg, libwaywallen-bridge,
  # the three renderers and `waywallen-layer-shell`; only the host GPU/display
  # stack is missing.
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

  # `ldd` on the AppDir binaries with LD_LIBRARY_PATH=$APPDIR/usr/lib reports
  # these as missing. The AppImage deliberately does not ship them because
  # Vulkan/GBM/DRM/EGL must match the running GPU driver.
  #   waywallen-layer-shell     : libwayland-client
  #   waywallen-video/image-renderer: libdrm, libgbm, libvulkan, libz
  #   waywallen-ui              : + libEGL/libGL/libGLX/libOpenGL, libdbus-1,
  #                               fontconfig, freetype, harfbuzz, libX11,
  #                               libxkbcommon
  # Most already arrive through appimageTools' FHS `multiPkgs`; they are listed
  # explicitly so the requirement is visible and independent of that list.
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

  # AppRun already execs `usr/bin/waywallen`, and the wrapper is named after
  # `pname`, so the desktop entry's `Exec=waywallen` works unchanged.
  extraInstallCommands = ''
    install -m 444 -D ${appDir}/usr/share/applications/org.waywallen.waywallen.desktop \
      $out/share/applications/org.waywallen.waywallen.desktop

    install -m 444 -D ${appDir}/usr/share/metainfo/org.waywallen.waywallen.metainfo.xml \
      $out/share/metainfo/org.waywallen.waywallen.metainfo.xml

    # Copy into the existing hicolor tree; `cp -r <dir> $out/share/` would nest
    # it as $out/share/icons/icons.
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

