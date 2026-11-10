{ lib, osConfig ? { }, ... }:

{
  options.my.firefox.nvidiaAcceleration = lib.mkOption {
    type = lib.types.bool;
    default = osConfig.hardware.nvidia.enabled or false;
    description = ''
      Expose the NVIDIA graphics device nodes and the matching DRM primary
      nodes inside the outer sandbox. Defaults to the NixOS NVIDIA driver
      selection. Disabling this keeps render-node-only device access, which
      can require software rendering on NVIDIA. Gecko sandboxes stay enabled.
    '';
  };

  options.my.firefox.networkPath = lib.mkOption {
    type = lib.types.enum [ "http-proxy" "transparent" ];
    default = "http-proxy";
    description = ''
      HTTP proxy preserves the Gost inlet and delegates target resolution.
      Transparent uses system DNS and the host's managed TUN/firewall path,
      enabling native HTTPS-RR/ECH discovery in Firefox. Requires TUN mode;
      it is an explicit generation-wide choice, never a proxy-error fallback.
    '';
  };

  options.my.firefox.videoSiteCompat = lib.mkOption {
    type = lib.types.bool;
    default = false;
    description = ''
      Send the origin-only Referer on cross-origin requests.
      Some media CDNs reject requests without it; disabled by default so
      cross-origin Referer stays fully suppressed.
    '';
  };
}
