{ pkgs, ... }:

{
  # Lock CPU frequency controls read-only (no one can force max freq/load).
  # scaling_setspeed only exists with the "userspace" governor, so we lock
  # every writable cpufreq node (governor/min/max/setspeed/boost) that exists.
  # Root can still write (chmod does not stop root), so cpupower/powertop
  # are unaffected.
  systemd.services.cpufreq-restrict = {
    description = "Restrict CPU scaling file access";
    after = [ "sys-kernel-debug.mount" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      # Use a script file: a multi-line `bash -c '...'` inside ExecStart trips
      # systemd's quote parsing ("Unbalanced quoting").
      ExecStart = "${pkgs.writeShellScript "cpufreq-restrict" ''
        for f in /sys/devices/system/cpu/cpu*/cpufreq/{scaling_governor,scaling_max_freq,scaling_min_freq,scaling_setspeed} /sys/devices/system/cpu/cpufreq/boost; do
          [ -e "$f" ] && chmod 444 "$f"
        done
      ''}";
    };
  };
}

