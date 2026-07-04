{ pkgs, ... }:

{
  environment.systemPackages = with pkgs; [
    # Serial communication
    cutecom
    minicom
    moserial
    putty

    # Simulate
    qemu_full

    # Assembly
    nasm

    # Generic embedded
    gcc-arm-embedded
    openocd
    platformio

    # STM32
    stm32flash
    stm32loader
    stlink
    stlink-gui
    stlink-tool

    # ESP32
    espflash
    esptool
    
    # Arduino
    arduino-cli
  ];
}

