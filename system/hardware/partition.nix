{pkgs, ...}:

{
  # File system
  fileSystems."/persist".neededForBoot = true;

  # impermanence
  environment.persistence."/persist" = {
    hideMounts = true;
    directories = [
      # suggested subdirectory breakdown
      "/etc/NetworkManager/system-connections"
      "/etc/nixos"
      "/etc/waydroid-extra"

      # If use SSH
      #"/etc/ssh"

      "/var/lib/AccountsService"
      "/var/lib/bluetooth"
      "/var/lib/cups"
      "/var/lib/NetworkManager"
      "/var/lib/nixos"
      "/var/lib/systemd/backlight"
      "/var/lib/systemd/coredump"
      "/var/lib/systemd/credential"
      "/var/lib/systemd/credentials"
      "/var/lib/systemd/linger"
      "/var/lib/systemd/random-seed"
      "/var/lib/tailscale"
      "/var/log"
    ];
    files = [
      "/etc/machine-id"

      # If use SSH
      # "/etc/ssh/ssh_host_rsa_key"
      # "/etc/ssh/ssh_host_ed25519_key"
    ];
  };

  boot.kernelParams = [
    "dm_mod.dm_mq_queue_depth=2048"
  ];

  boot.initrd.availableKernelModules = [ "tpm" "tpm_tis" "tpm_crb" ];
    
  boot.initrd.systemd.extraBin = {
    cryptsetup = "${pkgs.cryptsetup}/bin/cryptsetup";
    tpm2_pcrread = "${pkgs.tpm2-tools}/bin/tpm2_pcrread";
  };

  environment.systemPackages = with pkgs; [
    btrfs-progs disko tpm2-tools
  ];
}

