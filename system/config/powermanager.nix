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

  # amd_pstate active (EPP); ondemand is invalid there.
  # amd_dynamic_epp: driver auto-switches EPP by AC/battery & platform profile (PPD)
  boot.kernelParams = [ "amd_pstate=active" "amd_dynamic_epp=enable" ];

  # PPD
  services.power-profiles-daemon.enable = true;

  # Upower
  services.upower.enable = true;
}

