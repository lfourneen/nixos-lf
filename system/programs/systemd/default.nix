{
  imports = [
    ./btrfs-rollback.nix  
    ./cpufreq-restrict.nix  
    ./coredump.nix
    ./dns-upstream.nix  
    ./flatpak-mirror.nix  
    ./gost-relay.nix  
    ./initrd.nix  
    ./journald.nix
    ./libvirtd.nix  
    ./nix-daemon.nix
    ./netsec-alert.nix
    ./network-bootstrap.nix
    ./network-observe.nix
    ./nftables-recover.nix
    ./nftables-verify.nix
    ./ntsync.nix  
    ./nvidia-powerd.nix
    ./proxy-mode.nix
  ];
}

