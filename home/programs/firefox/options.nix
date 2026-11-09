{ lib, ... }:

{
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
