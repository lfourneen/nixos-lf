{
  imports = [
    #./amdgpu.nix
    ./audio.nix
    ./bluetooth.nix
    ./boot/initrd.nix
    ./boot/btrfs-rollback.nix
    ./bootloader.nix
    ./cpu.nix
    ./cpu/cpufreq-restrict.nix
    ./disko.nix
    ./driver-opt.nix
    ./partition.nix
    ./usbguard.nix
    ./nvidia.nix
    ./nvidia/nvidia-powerd.nix
  ];
}

