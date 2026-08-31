{pkgs, ...}:

{
  # File system
  fileSystems."/persist".neededForBoot = true;

  # Impermanence
  environment.persistence."/persist" = {
    hideMounts = true;
    
    directories = [
      # Suggested subdirectory breakdown
      "/etc/NetworkManager/system-connections"
      "/etc/nixos"
      "/etc/waydroid-extra"

      # If use SSH: ONLY if host keys are persisted instead of sops-managed
      # (default with ssh.nix = sops host key, so nothing here is needed;
      #  "/etc/ssh" recursively persists all host keys — no extra file entries)
      #"/etc/ssh"

      "/var/lib/AccountsService"
      "/var/lib/bluetooth"
      "/var/lib/cups"
      "/var/lib/hermes"
      "/var/lib/NetworkManager"
      "/var/lib/nixos"
      "/var/lib/systemd/backlight"
      "/var/lib/systemd/coredump"
      "/var/lib/systemd/credential"
      "/var/lib/systemd/credentials"
      "/var/lib/systemd/linger"
      "/var/log"
    ];
    
    files = [
      "/etc/machine-id"
      "/var/lib/systemd/random-seed"
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
    btrfs-progs 
    disko 
    tpm2-tools
  ];
}

