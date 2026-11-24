{ config, lib, pkgs, ... }:

let
  cfg = config.services.hermes-agent;
  user = "hermes-time";
  clients = "hermes-time-clients";
  uid = config.my.machine.uids.hermesTime;
  socket = "/run/hermes-time/mcp.sock";
  proxy = import ../../config/network/proxy/env.nix { inherit lib; };

  root = pkgs.runCommand "hermes-time-root" { } ''
    mkdir -p "$out"/{dev,nix/store,proc,run,sys,work}
  '';
  
in
lib.mkIf cfg.enable {
  assertions = [ {
    assertion = uid > 0 && lib.count (account: account.uid == uid) (lib.attrValues config.users.users) == 1;
    message = "Hermes time MCP needs a distinct non-root UID.";
  } ];

  users.users.${user} = {
    inherit uid;
    isSystemUser = true;
    group = user;
  };
  users.groups.${user} = { };
  users.groups.${clients}.members = [ cfg.user ];

  services.hermes-agent.settings.mcp_servers.time = {
    command = lib.getExe' pkgs.coreutils "env";
    args = [ "-i" (lib.getExe pkgs.socat) "STDIO" "UNIX-CONNECT:${socket}" ];
  };

  systemd.sockets.hermes-time = {
    description = "Hermes time MCP socket";
    wantedBy = [ "sockets.target" ];
    socketConfig = {
      ListenStream = socket;
      Accept = true;
      SocketUser = "root";
      SocketGroup = clients;
      SocketMode = "0660";
      DirectoryMode = "0755";
      RemoveOnStop = true;
    };
  };

  systemd.services."hermes-time@" = {
    description = "Hermes time MCP session";
    environment.HOME = "/work";
    serviceConfig = {
      User = user;
      Group = user;
      Slice = "untrusted.slice";
      ExecStart = lib.escapeShellArgs [
        (lib.getExe pkgs.mcp-server-time)
        "--local-timezone"
        (if config.time.timeZone == null then "UTC" else config.time.timeZone)
      ];
      StandardInput = "socket";
      StandardOutput = "inherit";
      StandardError = "journal";
      UnsetEnvironment = proxy.trustOverrideNames;

      RootDirectory = root;
      BindReadOnlyPaths = [ "/nix/store" ];
      TemporaryFileSystem = [ "/work:rw,nosuid,nodev,noexec,size=16M,mode=0700,uid=${toString uid}" ];
      WorkingDirectory = "/work";
      PrivateDevices = true;
      BindLogSockets = false;
      PrivateIPC = true;
      PrivateNetwork = true;
      RestrictAddressFamilies = [ "AF_UNIX" ];
      RestrictNamespaces = true;
      # Socket creation must also go through the address-family filter.
      SystemCallFilter = [ "~io_uring_setup io_uring_enter io_uring_register" ];
      SystemCallArchitectures = "native";
      ProtectProc = "invisible";
      ProcSubset = "pid";
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
    };
  };
}
