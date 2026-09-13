{ ... }:

{
  # Persistent journal, bounded by size + retention + rate limit. journald
  # enforces all of these itself, so the old custom vacuum timer is gone.
  # NOTE: systemd config files only accept full-line comments — a trailing
  # comment becomes part of the value and makes the setting be ignored.
  services.journald = {
    storage = "persistent";
    extraConfig = ''
      # Total on disk
      SystemMaxUse=512M
      # Rotate per file
      SystemMaxFileSize=64M
      # Auto-expire old entries
      MaxRetentionSec=1week
      # Cap the /run (tmpfs) journal
      RuntimeMaxUse=256M
      # Per-service flood protection
      RateLimitIntervalSec=30s
      RateLimitBurst=1000
    '';
  };
}

