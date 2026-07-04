{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
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
        hashedPasswordFile = "/persist/passwords/lfour";
        description = "LFour";
        extraGroups = [ "networkmanager" "wheel" "dialout"  "libvirtd" "video" "audio" "adbusers" "plugdev" "docker" "resolvconf" ];
        shell = unstable-pkgs.nushell;
      };
    };
  };
}

