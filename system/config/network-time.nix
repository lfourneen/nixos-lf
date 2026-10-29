{ config, ... }:

{
  # One authenticated clock client. Keep certificate time checks enabled;
  # -s restores the RTC/drift-file time floor after an offline reboot, and the
  # module tracks the RTC offset (rtcfile + rtcautotrim), so the floor stays
  # accurate without an unauthenticated NTP pool.
  services.chrony = {
    enable = true;
    enableNTS = true;
    servers = map (host: "${host} port ${toString config.my.machine.ports.ntp} ntsport ${toString config.my.machine.ports.nts}")
      config.my.machine.ntsUpstreams;
    extraFlags = [ "-s" ];
    extraConfig = ''
      authselectmode require
      nocerttimecheck 0
      port 0
    '';
  };
  systemd.services.chronyd = {
    requires = [ "network-firewall-bootstrap.service" ];
    after = [ "network-firewall-bootstrap.service" ];
  };
}
