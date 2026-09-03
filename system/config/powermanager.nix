{ ... }:

{
  # CPU
  powerManagement = {
    enable = true;
    # amd_pstate active: only powersave/performance; powersave = EPP via PPD
    cpuFreqGovernor = "powersave";
    powertop.enable = true;
    # Laptop middle ground; min_power can hang disks
    scsiLinkPolicy = "med_power_with_dipm";
    cpufreq = {
      max = 5180000; # allow 5.14GHz boost on demand
      min = 400000;
    };
  };

  # amd_pstate active (EPP); ondemand is invalid there
  boot.kernelParams = [ "amd_pstate=active" ];

  # PPD
  services.power-profiles-daemon.enable = true;

  # Upower
  services.upower.enable = true;
}

