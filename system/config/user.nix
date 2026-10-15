{ pkgs, ... }:

{
  # Define user account.
  users = {
    mutableUsers = false;
    groups.plugdev = {};

    users = {
      root = {
        hashedPasswordFile = "/persist/passwords/root";
      };

      lfour = {
        uid = 1000;
        isNormalUser = true;
        home = "/home/lfour";
        hashedPasswordFile = "/persist/passwords/lfour";
        description = "LFour";
        shell = pkgs.unstable.nushell;

        extraGroups = [ 
          "networkmanager" 
          "wheel" 
          "dialout" 
          "input" 
          "tty" 
          "i2c" 
          "libvirtd" 
          "video" 
          "audio" 
          "adbusers" 
          "plugdev" 
          "resolvconf" 
          "hermes" 
          "wireshark" 
        ];
      };

      # hermes (from the hermes-agent module) needs subuid/subgid for rootless
      # docker; 165536 keeps clear of lfour's auto range (100000+65536).
      hermes = {
        subUidRanges = [ { startUid = 165536; count = 65536; } ];
        subGidRanges = [ { startGid = 165536; count = 65536; } ];
      };
    };
  };
}

