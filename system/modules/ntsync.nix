{ pkgs, ... }:

{
  # Load ntsync later to avoid race condition
  systemd.services.ntsync = {
    description = "Load ntsync kernel module after udev";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-udevd.service" "local-fs.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.kmod}/bin/modprobe ntsync";
    };
  };
}

