{ pkgs, ... }:

{
  environment.systemPackages = with pkgs; [
    # Serial communication
    cutecom
    minicom

    # Reuse the host QEMU selected by libvirt.
    qemu_kvm

    # Assembly
    nasm

    # Generic embedded
    gcc-arm-embedded
    openocd

    # STM32
    stm32flash
    stm32loader
    stlink
    stlink-tool

    # ESP32
    espflash
    esptool
    
    # Arduino
    arduino-cli
  ];
}

