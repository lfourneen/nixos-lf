{
  imports = [
    # Folders
    #./fhs
    ./systemd

    # Files
    ./clash-verge.nix
    ./git.nix
    ./flatpak.nix
    ./hermes.nix
    ./misc.nix
    ./mpd.nix
    ./nix-ld.nix
    ./prgs.nix
    #./rustdesk.nix
    # SSH is intentionally off; enabling it also needs the `tcp dport 22` rule
    # in system/config/network.nix.
    #./ssh.nix
    ./steam.nix
    ./sunshine.nix
    ./udev.nix
    ./virtualisation.nix
  ];
}

