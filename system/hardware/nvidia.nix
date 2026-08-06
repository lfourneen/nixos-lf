{ config, pkgs, ... }:

{
  nixpkgs.config = {
    nvidia.acceptLicense = true;
    cudaSupport = true;
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
  services.xserver.videoDrivers = ["nvidia"];
 
  # Nvidia hardware settings
  hardware.nvidia = {
    package = config.boot.kernelPackages.nvidiaPackages.latest;
    modesetting.enable = true;
    dynamicBoost.enable = true;
    gsp.enable = true;
    powerManagement = {
      enable = true;
      finegrained = false;
    };
    open = true;
    nvidiaSettings = true;
    videoAcceleration = true;
    prime = {
      #offload = {
        #enable = true;
        #enableOffloadCmd = true;
      #};
      # Make sure to use the correct Bus ID values for your system!
      #amdgpuBusId = "PCI:6:0:0";
      #nvidiaBusId = "PCI:1:0:0";
    };
  };

  hardware.nvidia-container-toolkit = {
    enable = false;
  };
 
  # Bootloader
  boot.initrd.kernelModules = [ "nvidia" "nvidiafb" "nvidia_drm" "nvidia_uvm" "nvidia-modeset" ];
  
  # Fixed
  #boot.extraModulePackages = [ config.boot.kernelPackages.nvidia_x11 ];

  boot.blacklistedKernelModules = [ "nouveau" "amdgpu" ];
    
  # KernelParams
  boot.kernelParams = [ 
    "tsc=reliable"
    "nvidia-drm.fbdev=1"
    "nvidia.NVreg_TemporaryFilePath=/var/tmp"
    "transparent_hugepage=always"
  ];
}

