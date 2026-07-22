{ config, lib, ... }: 

{
  services.hermes-agent = {
    enable = true;
    addToSystemPackages = true;
    environmentFiles = [ "/etc/nixos/system/programs/secrets/hermes.env" ];
    #extraArgs = [ "--verbose" ];
    restart = "always";
    restartSec = 5;
    documents = {
      "USER.md" = "/home/lfour/Documents/Hermes/USER.md";
    };
    #container = {
      #enable = true;
      #image = "ubuntu:24.04";
      #backend = "docker";
      #hostUsers = [ "lfour" ];
      #extraVolumes = [ "/home/user/Hermes:/Hermes:rw" ];
      #extraOptions = [ "--gpus" "all" ];
    #};
    settings = {
      model = {
        base_url = "https://api.deepseek.com";
        default = "deepseek/deepseek-v4-pro";
      };
      toolsets = [ "all" ];
      terminal = { 
        backend = "local";
	cwd = "/var/lib/hermes";
	timeout = 180; 
      };
      compression = {
        enabled = true;
        threshold = 0.85;
        summary_model = "deepseek/deepseek-v4-flash";
      };
      display = {
        compact = false; 
	personality = "kawaii"; 
      };
      memory = { 
        memory_enabled = true; 
	user_profile_enabled = true;
      };
      agent = { 
        max_turns = 60; 
	verbose = false; 
      };
      plugins.enabled = [
        "hermes-lcm"
        "rtk-rewrite"
      ];
    };
  };

  systemd.services.hermes-agent.serviceConfig = {
    User = "hermes";
    Group = "hermes";
    ProtectSystem = "strict";
    ProtectHome = lib.mkForce false;
    SupplementaryGroups = [ "users" ];
    ReadWritePaths = [ "/home/lfour/Hermes" ];
    PrivateTmp = true;
    ProtectControlGroups = true;
    ProtectKernelModules = true;
    RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
  };
}

