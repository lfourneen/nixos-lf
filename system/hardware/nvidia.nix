{ config, pkgs, ... }:

let
  # Linux 7.2 removed strncpy(), which the 595.71.05 open kernel modules still
  # use. 595.99.02 is the first production release with Linux 7.2 support;
  # mirrors nixpkgs-unstable until it reaches nixos-26.05.
  nvidiaPackage = config.boot.kernelPackages.nvidiaPackages.mkDriver {
    version = "595.99.02";
    sha256_64bit = "sha256-6HR3lYv3YwcFSTJL1a1slI66btIQ5EAFs+/4SUD24ew=";
    sha256_aarch64 = "sha256-CCqHZTN2KNOZ4yZp2rDcuRJp9pHfRw47k4m4dWnS/2w=";
    openSha256 = "sha256-T36x/jx8yQ8l3LFp1rZIrTfcSwbGy8YSAvXOUSptpb4=";
    settingsSha256 = "sha256-GYCcnxfKPrTCrsmd25sMyzfC5cqJQJx0c31haooyTYM=";
    persistencedSha256 = "sha256-VyKtF/HdHPQrHHK6opSO69M72LmnGZtauuchj9uuje8=";
  };

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
  ];
}

