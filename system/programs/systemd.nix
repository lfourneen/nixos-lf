{ config, lib, pkgs, ... }:

{
    # SystemD initrd
    boot.initrd.systemd.enable = true;

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

  # Load ntsync later to avoid race condition
  systemd.services.ntsync = {
    description = "Load ntsync kernel module after udev";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-udevd.service" "local-fs.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.kmod}/bin/modprobe ntsync";
    };
  };

  # NVIDIA powed service
  systemd.services.nvidia-powerd = {
    after = [ "nvidia-persistenced.service" "multi-user.target" ];
    wantedBy = lib.mkForce [ "multi-user.target" ];
    serviceConfig = {
      ExecStartPre = "${pkgs.bash}/bin/bash -c 'while [ ! -e /dev/nvidiactl ]; do sleep 0.5; done'";
    };
  };

  # Restrict CPU frequency scaling permissions 
  systemd.services.cpufreq-restrict = {
    description = "Restrict CPU scaling file access";
    after = [ "sys-kernel-debug.mount" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.coreutils}/bin/chmod 444 /sys/devices/system/cpu/cpu*/cpufreq/scaling_setspeed";
    };
  };

# Network PAC (Fail-Open Architecture with Gost)
  systemd.services.gost-pac = {
    description = "Gost PAC High-Availability Proxy Daemon";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "lfour";
      Group = "users";
      StateDirectory = "gost";
      
      # Internal implementation of persistent loop monitoring and hot-reloading
      ExecStart = "${pkgs.writeShellScript "gost-launcher" ''
        current_status="none"
        proxy_pid=""

        # Cleanup handler to ensure child processes are terminated on service stop
        cleanup() {
          echo "Stopping proxy supervisor..."
          if [ -n "$proxy_pid" ]; then
            kill "$proxy_pid"
          fi
          exit 0
        }
        trap cleanup TERM INT

        # Infinite keepalive and health-check loop
        while true; do
          # Use --noproxy "*" to bypass global env variables and avoid Nftables/Zapret interference during probing
          if ${pkgs.curl}/bin/curl -sf -m 2 --noproxy "*" http://127.0.0.1:33331/commands/pac > /dev/null; then
            new_status="proxy"
          else
            new_status="direct"
          fi

          # Trigger hot-reload only when backend state changes
          if [ "$new_status" != "$current_status" ]; then
            echo "Proxy status changed from [$current_status] to [$new_status]. Reloading..."
            
            # Terminate the active gost instance cleanly before spawning a new one
            if [ -n "$proxy_pid" ]; then
              kill "$proxy_pid"
              wait "$proxy_pid" 2>/dev/null
            fi

            # Explicitly strip proxy env variables prior to execution to prevent infinite loop regressions
            if [ "$new_status" = "proxy" ]; then
              # Clash Online: Listen on 33332 and forward traffic to Clash core at 7897
              env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              ${pkgs.gost}/bin/gost -L=http://127.0.0.1:33332 -F=http://127.0.0.1:7897 &
            else
              # Clash Offline: Listen on 33332 and act as a standalone HTTP proxy for direct fallback
              env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              ${pkgs.gost}/bin/gost -L=http://127.0.0.1:33332 &
            fi
            
            proxy_pid=$!
            current_status="$new_status"
          fi

          # Health check interval (seconds)
          sleep 5
        done
      ''
      }";

      # Sandbox the supervisor process environment
      Environment = [
        "no_proxy=127.0.0.1,localhost,::1"
        "NO_PROXY=127.0.0.1,localhost,::1"
        "http_proxy="
        "https_proxy="
        "all_proxy="
        "HTTP_PROXY="
        "HTTPS_PROXY="
        "ALL_PROXY="
      ];
      Restart = "always";
      RestartSec = "5";
    };
  };

 # Flatpak
  systemd.services.flatpak-mirror = {
    description = "Configure Flathub USTC Mirror";
    wantedBy = [ "multi-user.target" ];
    before = [ "flatpak-managed-install.service" ]; 
    after = [ "network-online.target" "dbus.service" ]; 
    wants = [ "network-online.target" ];
    
    path = [ pkgs.flatpak ];
    script = ''
      flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
      flatpak remote-modify flathub --url=https://mirrors.ustc.edu.cn/flathub
    '';
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
  };

  # Libvirt
  systemd.services.libvirtd.serviceConfig = {
    LoadCredential = "";
    LoadCredentialEncrypted = "";
  };

  environment.systemPackages = with pkgs; [ 
    curl
    gnused
    gost
  ];
}

