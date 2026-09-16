{ pkgs, ... }:

{
  programs.codex = {
    enable = true;
    package = pkgs.unstable.codex;
    enableMcpIntegration = true;
    settings = {
      mcp_servers = {
        mcp-nixos = {
          command = "mcp-nixos";
	      };
      };
    };
  };
}

