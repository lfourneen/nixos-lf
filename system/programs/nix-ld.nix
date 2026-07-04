{ pkgs, ... }:

{
  # Nix-ld: Run closed-source Binary
  programs.nix-ld = {
    enable = true;
    libraries = with pkgs; [
      stdenv.cc.cc.lib 
      glib 
      gtk3
      libusb1
      libGL
      libGLU
      libGLX
      libxcb 
      libxcb-wm 
      libxcb-util
      libxcb-image 
      libxcb-cursor 
      libxcb-errors 
      libxcb-keysyms
      libxcb-render-util 
      libx11 
      libxscrnsaver 
      libxcomposite 
      libxkbcommon
      libxdamage 
      libxext 
      libxfixes 
      libxrandr 
      libxrender 
      libxtst
      libatomic_ops
      libayatana-appindicator
      nspr 
      nss 
      alsa-lib 
      cups 
      dbus 
      at-spi2-atk 
      at-spi2-core
      libdbusmenu 
      libdrm 
      libpulseaudio 
      mesa 
      pango 
      cairo 
      krb5
      fontconfig 
      udev 
      zlib
      zlib-ng
      libpng
      gtk3
      gtk4
      gdk-pixbuf
      gdk-pixbuf-xlib
    ];
  };
}

