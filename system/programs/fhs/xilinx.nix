{ config, libs, pkgs, ... }:

let
  XilinxRoot = "/home/lfour/FHS/Xilinx";
in
{
  environment.systemPackages = with pkgs; [
   # Xilinx FHS
    (buildFHSEnvBubblewrap {
      name = "vivado-fhs";
      chdir = XilinxRoot;
      targetPkgs = pkgs: with pkgs; [
        # Basic Tools & Shell
        bash bc coreutils
        file git gnumake
        nettools python313 unzip 
        which zstd 

        # Libraries & System
        boost dbus expat 
        gperftools libpng libusb1 
        libuuid libxcrypt-legacy libxv
        ncurses5 onnxruntime openssl 
        pixman protobuf_32 tcl-8_6 
        xercesc zlib

        # Compiler, Build & OpenCL
        gcc graphviz ocl-icd
        opencl-headers stdenv.cc.cc.lib stdenv.cc.cc tcl

        # Graphics & UI
        fontconfig freetype glib
        gtk2 gtk3 libGL
        libglvnd mesa
      ] ++ (with pkgs; [
        # X11 Libraries
        libICE libSM libX11
        libxcb libXcomposite libXScrnSaver 
        libXcursor libXdamage libXext 
        libXfixes libXft libXi 
        libXrandr libXrender libxshmfence 
        libXtst
      ]);
      extraBwrapArgs = [
        "--bind" "/run/udev" "/run/udev"
        "--bind-try" "/var/run/dbus" "/var/run/dbus"
        "--dev-bind" "/dev" "/dev"
        "--bind" "/sys" "/sys"
        "--bind" "/proc" "/proc"
      ];
      runScript = "bash";
      profile = ''
        export FHS=1
        export LANG=en_US.UTF-8
        export LC_ALL=en_US.UTF-8
        export _JAVA_OPTIONS="-Dawt.useSystemAAFontSettings=on -Dswing.aatext=true"

        XILINX_LIBS=(
          "${XilinxRoot}/2025.2/Vitis/lib/lnx64.o"
          "${XilinxRoot}/2025.2/Vitis/lib/lnx64.o/Ubuntu/24"
          "${XilinxRoot}/2025.2/Vivado/lib/lnx64.o"
          "${XilinxRoot}/2025.2/Vivado/lib/lnx64.o/Ubuntu/24"
          "${XilinxRoot}/2025.2/PDM/lib/lnx64.o/Ubuntu/24"
          "${XilinxRoot}/2025.2/Model_Composer/lib/lnx64.o/Ubuntu/24"
          "${XilinxRoot}/xic/lib/lnx64.o/Ubuntu/24"
        )
        NEW_PATHS=$(IFS=:; echo "''${XILINX_LIBS[*]}")
        NEW_PATHS="$NEW_PATHS:/run/opengl-driver/lib:${pkgs.lib.makeLibraryPath (with pkgs; [
          ncurses5
          libxcrypt-legacy
          openssl
          stdenv.cc.cc.lib
          gtk2
          libsecret
          libGL
          libglvnd
          zlib
          libuuid
        ])}"
        export LD_LIBRARY_PATH="$NEW_PATHS:$LD_LIBRARY_PATH"
      '';
    })
  ];
}

