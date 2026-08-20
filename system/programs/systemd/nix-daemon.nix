{ ... }:

{
  # Route nix-daemon downloads via gost-pac (127.0.0.1:33332) to avoid
  # zapret breaking large TLS streams (onnxruntime/nccl mid-download drops)
  systemd.services.nix-daemon = {
    description = "Nix Daemon (downloads via gost-pac proxy)";
    # Socket-activated: only orders boot-time startup; gost-pac is fail-open (direct when Clash down), so no hard dep
    after = [ "gost-pac.service" ];
    environment = {
      HTTP_PROXY = "http://127.0.0.1:33332/";
      HTTPS_PROXY = "http://127.0.0.1:33332/";
      NO_PROXY = "127.0.0.1,localhost,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10";
    };
  };
}
