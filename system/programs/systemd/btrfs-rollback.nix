{ pkgs, ... }:

{    
  # BTRFS ephemeral root
  boot.initrd.systemd.extraBin.btrfs = "${pkgs.btrfs-progs}/bin/btrfs"; # Ensure btrfs tool is available in initrd
  boot.initrd.systemd.services.rollback = {
    description = "Rollback BTRFS root subvolume to a pristine state";
    wantedBy = [ "initrd.target" ];
    after = [ "systemd-cryptsetup@enc.service" ];
    before = [ "sysroot.mount" ];
    unitConfig.DefaultDependencies = "no";
    serviceConfig.Type = "oneshot";
    script = ''
      export PATH=/bin:/sbin:/usr/bin:/usr/sbin:$PATH
      set -euo pipefail

      mkdir -p /btrfs_tmp
      mount -o subvolid=5 /dev/mapper/enc /btrfs_tmp

      # Ensure /sysroot is not mounted before we delete the subvolume
      if mountpoint -q /sysroot 2>/dev/null; then
        echo "Warning: /sysroot is already mounted, unmounting it to avoid conflicts..."
        umount /sysroot || true
      fi

      if [[ -d /btrfs_tmp/root ]]; then
        echo "Removing existing root subvolume and all descendants recursively..."
        btrfs subvolume delete -R /btrfs_tmp/root
      fi

      echo "Creating new pristine root subvolume..."
      btrfs subvolume create /btrfs_tmp/root

      umount /btrfs_tmp
      rmdir /btrfs_tmp
    '';
  };
}
