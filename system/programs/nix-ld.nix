{ pkgs, ... }:

{
  environment.extraInit = ''
    export XDG_DATA_DIRS="$XDG_DATA_DIRS:${pkgs.gtk3}/share/gsettings-schemas/${pkgs.gtk3.name}:${pkgs.gtk4}/share/gsettings-schemas/${pkgs.gtk4.name}"
  '';

  # Nix-ld: Run closed-source Binary
  programs.nix-ld = {
    enable = true;
    package = pkgs.nix-ld;
    libraries = with pkgs; [
      # Core Runtime and Base C Libraries
      glib
      gobject-introspection
      icu
      libatomic_ops
      libgcc
      libxml2
      libxslt
      stdenv.cc.cc.lib
      zlib
      zlib-ng

      # System Services and Hardware Integration
      alsa-lib
      cups
      dbus
      krb5
      libdbusmenu
      libdbusmenu-gtk3
      libpulseaudio
      libsecret
      libusb1
      libuuid
      nspr
      nss
      openssl
      shared-mime-info
      systemd
      udev

      # Graphics Rendering and Core Display Stack
      extest
      libGL
      libGLU
      libGLX
      libdrm
      libexecinfo
      libexif
      libexosip
      libexsid
      libextractor
      libexttextcat
      libgbm
      libx11
      libxcomp
      libxcomposite
      libxcursor
      libxdamage
      libxdmcp
      libxeddsa
      libxext
      libxfixes
      libxft
      libxi
      libxinerama
      libxisf
      libxrandr
      libxrender
      libxt
      mesa

      # X11 Extensions and Multimedia Window Support
      libICE
      libSM
      libxcomposite
      libxdamage
      libxkbcommon
      libxmu
      libxp
      libxrandr
      libxscrnsaver
      libxtst
      libxv
      libxxf86vm

      # XCB Protocol Components
      libxcb
      libxcb-cursor
      libxcb-errors
      libxcb-image
      libxcb-keysyms
      libxcb-render-util
      libxcb-util
      libxcb-wm

      # Fonts and 2D Rendering
      cairo
      fontconfig
      freetype
      gdk-pixbuf
      gdk-pixbuf-xlib
      harfbuzz
      libpng
      pango

      # GTK Desktop Toolkit and Accessibility
      at-spi2-atk
      at-spi2-core
      gsettings-desktop-schemas
      gtk3
      gtk4
      libayatana-appindicator
      xdg-desktop-portal
      xdg-desktop-portal-gnome
      xdg-desktop-portal-gtk
      xdg-desktop-portal-shana
      xdg-desktop-portal-termfilechooser
      xdg-desktop-portal-wlr

      # Networking and WebKit Browser Engine
      libsoup_3
      webkitgtk_4_1

      # GStreamer Media Framework
      gst_all_1.gst-plugins-base
      gst_all_1.gst-plugins-good
      gst_all_1.gst-vaapi
      gst_all_1.gstreamer

      # Development Tools
      openjdk

      # Desktop Notifications and Indicators
      libappindicator
      libappindicator-gtk3
      libnotify

      # Sound Support
      libcanberra
      libcanberra-gtk3
      sound-theme-freedesktop

      # Filesystem Utilities
      e2fsprogs

      # 32-bit
      pkgsi686Linux.glibc
      pkgsi686Linux.libgcc
    ];
  };
}

