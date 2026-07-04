{ ... }:

{
  # CPU
  powerManagement.cpuFreqGovernor = "performance";

  # Zram (but no hibernate)
  zramSwap.enable = true;

  # Nohang
  #services.nohang.enable = true;
 
  # PPD
  services.power-profiles-daemon.enable = true;

  # Tlp
  services.tlp = {
      enable = false;
      settings = {
        TLP_DEFAULT_MODE = "BAT";
        TLP_PERSISTENT_DEFAULT = 1;
        CPU_SCALING_GOVERNOR_ON_AC = "performance";
        CPU_SCALING_GOVERNOR_ON_BAT = "powersave";
        CPU_ENERGY_PERF_POLICY_ON_BAT = "power";
        CPU_ENERGY_PERF_POLICY_ON_AC = "performance";
        CPU_MIN_PERF_ON_AC = 0;
        CPU_MAX_PERF_ON_AC = 100;
        CPU_MIN_PERF_ON_BAT = 0;
        CPU_MAX_PERF_ON_BAT = 20;
        # Optional helps save long term battery health
        START_CHARGE_THRESH_BAT0 = 50; # 50 and bellow it starts to charge
        STOP_CHARGE_THRESH_BAT0 = 95;  # 100 and above it stops charging
      };
  };

   #Auto-cpufreq
   services.auto-cpufreq = {
     enable = false;
     settings = {
       battery = {
         governor = "powersave";
         turbo = "never";
       };
       charger = {
         governor = "performance";
         turbo = "auto";
       };
     };
  };

  # Tuned
  services.tuned.enable = false;

  # Upower
  services.upower.enable = true;
}

