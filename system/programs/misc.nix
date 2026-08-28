{ pkgs, ... }:

{
  # Profiling (with sysprof)
  services.sysprof.enable = true;

  # Fstrim
  services.fstrim = {
    enable = true;
    interval = "weekly";
  };

  # Ananicy
  services.ananicy = {
    enable = true;
    package = pkgs.ananicy-cpp;
  };

  # KMSCon
  services.kmscon = {
    enable = true;
    fonts = [ { name = "Maple Mono NF CN"; package = pkgs.maple-mono.NF-CN; } ];
    
    extraConfig = ''
      font-engine=pango
      font-size=12
    '';
  };

  # Firmware Updates
  services.fwupd.enable = true;

  # CUPS printing
  services.printing.enable = true;

  environment.systemPackages = with pkgs; [
    mpc
    sysprof
  ];
}

