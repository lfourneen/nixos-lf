{ ... }:

{
  # Persistent journal, bounded by size + retention + rate limit. journald
  # enforces all of these itself, so the old custom vacuum timer is gone.
  # NOTE: no inline comments in the settings below — systemd's config parser
  # does not support trailing comments and would ignore the whole line.
  services.journald = {
    storage = "persistent";
    extraConfig = ''
      SystemMaxUse=512M
      SystemMaxFileSize=64M
      MaxRetentionSec=1week
      RuntimeMaxUse=256M
      RateLimitIntervalSec=30s
      RateLimitBurst=1000
    '';
  };
}
