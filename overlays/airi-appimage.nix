{ pkgs, ... }:

let
  pname = "airi";
  version = "0.11.0";
  src = ./local-apps/AIRI-${version}-linux-x86_64.AppImage;
  appimageContents = pkgs.appimageTools.extract { inherit pname version src; };
in
pkgs.appimageTools.wrapType2 {
  inherit pname version src;

  extraPkgs = pkgs: with pkgs; [
    bash
    # Electron host system shared libraries
    alsa-lib
    at-spi2-atk
    at-spi2-core
    atk
    cairo
    cups
    dbus
    expat
    fontconfig
    freetype
    gtk3
    libdrm
    libgbm
    libpng
    libxkbcommon
    nspr
    nss
    pango
    zlib

    # OpenGL / EGL / Vulkan (GPU rendering)
    libGL
    libglvnd
    mesa

    # Vulkan (for GPU accelerated WebGL)
    vulkan-loader

    # VA-API (hardware video decode)
    libva

    # X11 host libraries
    libx11
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxrandr
    libxcb
    libxcursor
    libxi
    libxt
    libxtst

    # Wayland host support
    wayland

    # XWayland bridge (fallback when Wayland native is unavailable)
    xwayland
    egl-wayland
    egl-wayland2

    # Input method
    fcitx5-gtk

    # Audio
    pipewire

    # DRI / GPU driver access
    libGLU
  ];

  extraInstallCommands = ''
    # Wrap binary with Wayland env and flags
    mv $out/bin/${pname} $out/bin/.${pname}-wrapped
   
    cat > $out/bin/${pname} << WRAPPER
#!/bin/sh
export ELECTRON_OZONE_PLATFORM_HINT=auto

unset XDG_ACTIVATION_TOKEN
unset DESKTOP_STARTUP_ID

setsid $out/bin/.${pname}-wrapped \\
  --ozone-platform=wayland \\
  --enable-features=UseOzonePlatform,WebGL2ComputeContext \\
  --no-sandbox \\
  "\$@" > /tmp/airi-app-output.log 2>&1 < /dev/null &
WRAPPER

    chmod +x $out/bin/${pname}

    # Install desktop file
    for desktop in ${appimageContents}/*.desktop; do
      if [ -f "$desktop" ]; then
        desktopname=$(basename "$desktop")
        install -m 444 -D "$desktop" -t $out/share/applications
        substituteInPlace $out/share/applications/$desktopname \
          --replace-fail "Exec=AppRun" "Exec=$out/bin/${pname}" \
          --replace " %U" "" \
          --replace " %u" ""
          
        sed -i '/DBusActivatable/d' $out/share/applications/$desktopname
      fi
    done

    # Install icons
    for icon in ${appimageContents}/*.png; do
      if [ -f "$icon" ]; then
        iconname=$(basename "$icon")
        size=''${iconname%.png}
        case "$size" in
          *x*) ;; # already has size
          *) size="256x256" ;;
        esac
        install -m 444 -D "$icon" "$out/share/icons/hicolor/$size/apps/${pname}.png"
      fi
    done
  '';

  meta = {
    mainProgram = pname;
    description = "AIRI is an AI VTuber/Waifu chatbot supporting Live2D/VRM avatars, featuring human-like interactions and modular stage-based rendering.";
    homepage = "https://airi.moeru.ai";
    platforms = pkgs.lib.platforms.linux;
    license = pkgs.lib.licenses.mit;
  };
}

