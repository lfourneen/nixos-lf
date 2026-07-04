{ ... }:

{
  disko.devices = {
    disk = {
      main = {
        type = "disk";
        device = "/dev/nvme0n1";
        content = {
          type = "gpt";
          partitions = {
            ESP = {
              size = "1G";
              type = "EF00";
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/boot";
                mountOptions = [ "umask=0077" ];
              };
            };
            swap = {
              size = "16G";
              content = {
                type = "luks";
                name = "enc-swap";
                extraFormatArgs = [ "--cipher" "aes-xts-plain64" "--key-size" "512" ];
                settings = {
                  crypttabExtraOpts = [ "tpm2-device=auto" ];
                };
                content = {
                  type = "swap";
                };
              };
            };
            nixos = {
              size = "100%";
              content = {
                type = "luks";
                name = "enc";
                extraFormatArgs = [ "--cipher" "aes-xts-plain64" "--key-size" "512" ];
                settings = {
                  crypttabExtraOpts = [ "tpm2-device=auto" ];
                };
                content = {
                  type = "btrfs";
                  extraArgs = [ "-f" "-L" "nixos" ];
                  subvolumes = {
                    "/containerd" = {
                      mountpoint = "/var/lib/containerd";
                      mountOptions = [ "subvol=containerd" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                    "/containers" = {
                      mountpoint = "/var/lib/containers";
                      mountOptions = [ "subvol=containers" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                    "/docker" = {
                      mountpoint = "/var/lib/docker";
                      mountOptions = [ "subvol=docker" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                    "/flatpak" = {
                      mountpoint = "/var/lib/flatpak";
                      mountOptions = [ "subvol=flatpak" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                    "/home" = {
                      mountpoint = "/home";
                      mountOptions = [ "subvol=home" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                    "/libvirt" = {
                      mountpoint = "/var/lib/libvirt";
                      mountOptions = [ "subvol=libvirt" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                    "/waydroid" = {
                      mountpoint = "/var/lib/waydroid";
                      mountOptions = [ "subvol=waydroid" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                    "/nix" = {
                      mountpoint = "/nix";
                      mountOptions = [ "subvol=nix" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                    "/persist" = {
                      mountpoint = "/persist";
                      mountOptions = [ "subvol=persist" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                    "/root" = {
                      mountpoint = "/";
                      mountOptions = [ "subvol=root" "compress=zstd:3" "noatime" "discard=async" "space_cache=v2" "ssd" "commit=120" ];
                    };
                  };
                };
              };
            };
          };
        };
      };
    };
  };
}

