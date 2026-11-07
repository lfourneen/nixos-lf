{ lib, ... }:

{
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
