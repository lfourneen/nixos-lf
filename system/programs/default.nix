{
  imports = [
    # Folders
    ./clash
    ./fhs
    ./hermes
    ./systemd
    ./gost

    # Files
    ./git.nix
    ./flatpak
    ./misc.nix
    ./mpd.nix
    ./nix-ld.nix
    ./prgs.nix
    #./rustdesk.nix
    # The desktop SSH policy is declared in system/config/network/host.nix.
    #./ssh.nix
    ./sops.nix
    ./steam.nix
    ./sunshine.nix
    ./udev.nix
    ./virtualisation
  ];
}
