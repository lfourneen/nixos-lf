{ ... }:

{
  # RustDesk self-hosted server (signal + relay)
  services.rustdesk-server = {
    enable = true;
    openFirewall = false;  # 本机不用 iptables 防火墙(networking.firewall 已关闭),端口由 config/network.nix 的 nftables 规则开放
    signal.enable = true;
    relay.enable = true;
  };
}
