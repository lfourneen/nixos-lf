{ pkgs, ... }:
let
  python = pkgs.python3.withPackages (p: [ p.pygobject3 ]);
  backend = pkgs.runCommand "firefox-test-portal" { } ''
    mkdir -p "$out/share/xdg-desktop-portal/portals"
    cat > "$out/share/xdg-desktop-portal/portals/test.portal" <<EOF
    [portal]
    DBusName=org.freedesktop.impl.portal.desktop.test
    Interfaces=org.freedesktop.impl.portal.FileChooser;
    UseIn=test;
    EOF
  '';

in
{
  users.users.lfour.linger = true;
  services.dbus.enable = true;
  security.polkit.enable = true;
  xdg.portal = {
    enable = true;
    extraPortals = [ backend ];
    config.common.default = [ "test" ];
  };
  systemd.user.services.test-portal = {
    wantedBy = [ "default.target" ];
    serviceConfig.ExecStart = "${python}/bin/python3 ${./firefox_portal_fixture.py}";
    environment.GI_TYPELIB_PATH = "${pkgs.glib.out}/lib/girepository-1.0";
  };
  systemd.user.services.test-wayland = {
    wantedBy = [ "default.target" ];
    serviceConfig.ExecStart = "${pkgs.weston}/bin/weston --backend=headless --renderer=pixman --socket=wayland-0 --idle-time=0";
  };
  systemd.user.services.test-audio = {
    wantedBy = [ "default.target" ];
    environment.PULSE_RUNTIME_PATH = "/run/user/1000/pulse";
    serviceConfig.ExecStart = "${pkgs.pulseaudio}/bin/pulseaudio --daemonize=no --exit-idle-time=-1 --log-target=stderr --file=${pkgs.writeText "test-pulse.pa" ''
      load-module module-native-protocol-unix
      load-module module-null-sink
    ''}";
  };
}
