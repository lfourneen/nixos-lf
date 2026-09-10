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

  # Ling-long shop
  services.linyaps = {
    enable = true;
    package = pkgs.unstable.linyaps;
    boxPackage = pkgs.unstable.linyaps-box;
    webStoreInstallerPackage = pkgs.unstable.linyaps-web-store-installer;
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

  #services.comfyui = {
    #enable = true;
    #package = pkgs.unstable.comfyui;
    #listen = [ "127.0.0.1" "::1" ];
    #port = 8188;
    #dataDir = "/var/lib/comfyui";
  #};

  # Firmware Updates
  services.fwupd.enable = true;

  # CUPS printing
  services.printing.enable = true;

  environment.systemPackages = with pkgs; [
    mpc
    sysprof
  ];
}

