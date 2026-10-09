{ config, lib, pkgs, ... }:

let
  # Questa*-Altera FPGA Edition (Standard/Starter). Install it with the vendor
  # BitRock installer first:
  #   chmod +x overlays/local-apps/QuestaSetup-25.1std.0.1129-linux.run
  #   overlays/local-apps/QuestaSetup-25.1std.0.1129-linux.run \
  #     --mode unattended --unattendedmodeui none \
  #     --installdir "$HOME/FHS/altera/25.1std" \
  #     --accept_eula 1 --questa_edition questa_fse
  # That yields <installdir>/questa_fse/{bin,linux_x86_64,...}.
  questaVersion = "25.1std";
  questaEdition = "questa_fse";
  questaInstall = "${config.users.users.lfour.home}/FHS/altera/${questaVersion}";
  questaRoot = "${questaInstall}/${questaEdition}";

  # Licensing. 2025.1+ dropped LM_LICENSE_FILE / MGLS_LICENSE_FILE in favour of
  # the SALT variable. Unattended .dat files issued by the Altera SSLC (FIXED,
  # HOSTID = the wired NIC ens1) live in ${licenseDir}. The filename is an
  # account-identifying reference number, so it is deliberately NOT hardcoded:
  # when saltLicenseServer is null we pick the first *.dat at shell start.
  # Set saltLicenseServer for an explicit value instead, e.g. a floating
  # server "27000@license-host".
  saltLicenseServer = null;
  licenseDir = "${questaRoot}/license";
  licenseExport = lib.optionalString (saltLicenseServer != null)
    "export SALT_LICENSE_SERVER=\"${saltLicenseServer}\"";
  licenseGlobExport = lib.optionalString (saltLicenseServer == null) ''
    for _questa_lic in "${licenseDir}"/*.dat; do
      [ -e "$_questa_lic" ] && export SALT_LICENSE_SERVER="$_questa_lic" && break
    done
    unset _questa_lic
  '';

  # Sandboxed runtime exposing every native library the vendor binary needs.
  # `vsim`/`vlog`/`vcom` (batch) mostly need glibc; `vsim -gui`/`vish` (Tk) need
  # the X11 + font stack, and `visualizer` needs Qt5.
  questaFhs = pkgs.buildFHSEnvBubblewrap {
    name = "questa-fhs";
    chdir = questaInstall;
    targetPkgs = pkgs: with pkgs; [
      # Basic Utilities & Shell
      bash coreutils file findutils which

      # C++ runtime (vish / visualizer link libstdc++ / libgcc_s)
      stdenv.cc.cc stdenv.cc.cc.lib

      # System & Cryptography
      dbus libuuid zlib

      # Graphics, Fonts & UI
      expat fontconfig freetype
      gtk2 libGL libglvnd mesa

      # Qt5 (Visualizer)
      qt5.qtbase
    ] ++ (with pkgs; [
      # X11 Libraries
      libICE libSM libX11 libXau
      libXdmcp libXext libXft libXi
      libXrandr libXrender libXcursor
      libxcb xcbutil
    ]);
    extraBwrapArgs = [
      "--bind" "/run/udev" "/run/udev"
      "--bind-try" "/var/run/dbus" "/var/run/dbus"
      "--dev-bind" "/dev" "/dev"
    ];
    runScript = "bash";
    profile = ''
      export FHS=1
      export LANG=en_US.UTF-8
      export LC_ALL=en_US.UTF-8
      export MTI_HOME="${questaRoot}"
      # Vendor libs first: the tool ships its own Tcl/Tk, ACE and a GCC 10 runtime.
      export LD_LIBRARY_PATH="${questaRoot}/linux_x86_64:${questaRoot}/lib:${questaRoot}/gcc-10.3.0-linux_x86_64/lib:${questaRoot}/gcc-10.3.0-linux_x86_64/lib64:/run/opengl-driver/lib:$LD_LIBRARY_PATH"
      export PATH="${questaRoot}/bin:$PATH"
      # License (see saltLicenseServer above). Old LM_LICENSE_FILE is ignored
      # from 2025.1 onwards.
      ${licenseExport}
      ${licenseGlobExport}
      unset XDG_SESSION_TYPE
      unset XDG_CURRENT_DESKTOP
      unset GDK_BACKEND
      unset MOZ_ENABLE_WAYLAND
    '';
  };

  # `questa` command that jumps straight into the GUI instead of dropping you
  # into the FHS shell where you still have to `vsim -gui` yourself. Output is
  # logged and, on failure, a desktop notification is raised so launching from
  # Noctalia's search does not silently do nothing (e.g. a license error).
  questa = pkgs.writeShellScriptBin "questa" ''
    log="''${XDG_CACHE_HOME:-$HOME/.cache}/questa.log"
    mkdir -p "$(dirname "$log")"
    ${questaFhs}/bin/questa-fhs -c 'cd "${questaInstall}" && exec "${questaRoot}/bin/vsim" -gui' 2>&1 | tee "$log"
    rc=''${PIPESTATUS[0]}
    if [ "$rc" -ne 0 ]; then
      ${pkgs.libnotify}/bin/notify-send -u critical "Questa" "vsim exited ($rc). See $log" 2>/dev/null || true
    fi
  '';

  # Launcher icon so the icon theme can find it.
  questaIcon = pkgs.runCommandLocal "questa-icon" { } ''
    install -Dm644 ${./icons/questa-icon.png} $out/share/icons/hicolor/512x512/apps/questa.png
  '';

  # Desktop entry so Noctalia's application search lists and launches Questa.
  questaDesktop = pkgs.makeDesktopItem {
    name = "questa";
    desktopName = "Questa";
    genericName = "FPGA / RTL Simulator";
    comment = "Siemens Questa*-Altera FPGA Edition ${questaVersion}";
    exec = "${questa}/bin/questa";
    icon = "questa";
    terminal = false;
    categories = [ "Development" "Electronics" ];
    type = "Application";
  };

in
{
  environment.systemPackages = [
    questaFhs
    questa
    questaIcon
    questaDesktop
  ];
}

