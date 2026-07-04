{ config, ... }:
{
  boot.extraModulePackages = with config.boot.kernelPackages; [
    v4l2loopback
  ];

  boot.kernelModules = [ "v4l2loopback" ];

  boot.extraModprobeConfig = ''
    options v4l2loopback video_nr=10 card_label="PhoneCam" exclusive_caps=1
  '';
}

