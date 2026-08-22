{ pkgs, ... }:

{
  # Restrict CPU frequency scaling permissions 
  systemd.services.cpufreq-restrict = {
    description = "Restrict CPU scaling file access";
    after = [ "sys-kernel-debug.mount" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.coreutils}/bin/chmod 444 /sys/devices/system/cpu/cpu*/cpufreq/scaling_setspeed";
    };
  };
}

