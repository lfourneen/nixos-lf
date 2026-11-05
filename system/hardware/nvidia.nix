{ config, pkgs, ... }:

let
  nvidiaPackage =
    (pkgs.unstable.linuxPackagesFor config.boot.kernelPackages.kernel).nvidiaPackages.latest;

  # Restores the full FP16/BF16/TF32 tensor-core throughput that GeForce cards
  # ship capped. Loaded from the initrd *before* nvidia binds the GPU.
  # Recipe: nixpkgs doc/languages-frameworks/cuda.section.md
  #   § "Unlocking GeForce tensor core throughput"
  gpuUnlock =
    (pkgs.unstable.linuxPackagesFor config.boot.kernelPackages.kernel).gpu-unlock;

in
{
  nixpkgs.config = {
    nvidia.acceptLicense = true;
    cudaSupport = true;   # Disable it in the first build
  };

  # Enable OpenGL
  hardware.graphics = {
    enable = true;
    enable32Bit = true;

    extraPackages = with pkgs; [
      libva
      libva-utils
      libva-vdpau-driver
      libvdpau
      libvdpau-va-gl
      nvidia-vaapi-driver
      egl-wayland
      egl-wayland2
    ];
  };

  # Load nvidia driver for Xorg and Wayland
  services.xserver.videoDrivers = [ "nvidia" ];

  # Nvidia hardware settings
  hardware.nvidia = {
    package = nvidiaPackage;
    modesetting.enable = true;
    dynamicBoost.enable = true;
    gsp.enable = true;
    open = true;
    nvidiaSettings = true;
    videoAcceleration = true;

    powerManagement = {
      enable = true;
      finegrained = false;
    };
  };

  hardware.nvidia-container-toolkit = {
    enable = false;
  };

  # Bootloader
  boot.initrd.kernelModules = [
    "nvidia"
    "nvidiafb"
    "nvidia_drm"
    "nvidia_uvm"
    "nvidia-modeset"
  ];

  # gpu-unlock needs to run in the initrd, before systemd-modules-load pulls in
  # nvidia; the ordering comes from this oneshot unit, not from
  # boot.initrd.kernelModules (which would race with nvidia).
  boot.extraModulePackages = [ gpuUnlock ];
  hardware.firmware = [ gpuUnlock.firmware ];
  boot.initrd.availableKernelModules = [ "gpu_unlock" ];

  boot.initrd.systemd.services.gpu-unlock = {
    description = "Unlock NVIDIA GPU tensor core throughput";
    wantedBy = [ "sysinit.target" ];
    before = [
      "sysinit.target"
      "systemd-modules-load.service"
      "systemd-udevd.service"
      "systemd-udev-trigger.service"
    ];
    unitConfig.DefaultDependencies = false;
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "/bin/modprobe gpu_unlock";
    };
  };

  # Fixed
  #boot.extraModulePackages = [ config.boot.kernelPackages.nvidia_x11 ];

  boot.blacklistedKernelModules = [
    "nouveau"
    "amdgpu"
  ];

  # KernelParams
  boot.kernelParams = [
    "tsc=reliable"
    "nvidia-drm.fbdev=1"
    "nvidia.NVreg_TemporaryFilePath=/var/tmp"
    "transparent_hugepage=always"
    # Opt-in for gpu_unlock; drop this line to leave the GPU completely untouched.
    "gpu_unlock.targets=0000:01:00.0"
  ];
}

