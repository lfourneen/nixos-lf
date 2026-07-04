{ config, pkgs, ... }:

{
  nixpkgs.config.nvidia.acceptLicense = true;

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
    extraPackages32 = with pkgs; [
      libva 
      libva-utils
      libva-vdpau-driver
      libvdpau
      libvdpau-va-gl
      nvidia-vaapi-driver
    ];
  };

  # Load nvidia driver for Xorg and Wayland
  services.xserver.videoDrivers = ["nvidia"];
 
  # Nvidia hardware settings
  hardware.nvidia = {
    package = config.boot.kernelPackages.nvidiaPackages.latest;
    modesetting.enable = true;
    dynamicBoost.enable = true;
    powerManagement = {
      enable = true;
      finegrained = false;
    };
    open = true;
    nvidiaSettings = true;
    videoAcceleration = true;
    #prime = {
      #offload = {
        #enable = true;
        #enableOffloadCmd = true;
      #};
      # Make sure to use the correct Bus ID values for your system!
      #amdgpuBusId = "PCI:6:0:0";
      #nvidiaBusId = "PCI:1:0:0";
    #};
  };

  hardware.nvidia-container-toolkit = {
    enable = false;
  };
 
  # Bootloader
  boot.initrd.kernelModules = [ "nvidia" "nvidia_modeset" "nvidia_drm" "nvidia_uvm" ];
  
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
    
  #Screen Tearing Issues
  #hardware.nvidia.forceFullCompositionPipeline = true;

  environment.systemPackages = with pkgs; [
    # Vulkan
    vulkan-caps-viewer 
    vulkan-extension-layer 
    vulkan-headers 
    vulkan-loader 
    vulkan-memory-allocator 
    vulkan-tools 
    vulkan-tools-lunarg 
    vulkan-utility-libraries 
    vulkan-validation-layers 
    vulkan-volk 

    # CUDA Compilation & Runtime
    cudaPackages.cuda_nvcc        # CUDA compiler for JIT extensions
    cudaPackages.cuda_cudart      # Core CUDA runtime library
    cudaPackages.cuda_nvrtc       # Runtime compilation for dynamic kernels

    # CUDA Deep Learning Kernels
    cudaPackages.libcublas        # High-performance BLAS for matrix multiplication
    cudaPackages.cudnn            # Accelerated primitives for deep neural networks

    # CUDA Math & Development Utilities
    cudaPackages.libcurand        # Random number generation
    cudaPackages.libcusolver      # Numerical solver for linear systems
    cudaPackages.cuda_nvtx        # Instrumentation for performance profiling
  ];
}

