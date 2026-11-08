# Default MIME type / application assignments, shipped as /etc/xdg/mimeapps.list
# by system/config/desktop.nix.
#
# They are declared in the *system* layer on purpose. GLib persists "set as
# default" by rewriting $XDG_CONFIG_HOME/mimeapps.list in place, so that path
# must stay a real, writable file. home-manager's xdg.mimeApps made it a
# read-only /nix/store symlink, which turned every GUI/CLI default change into
# "Failed to create file ...: Read-only file system".
#
# /etc/xdg/mimeapps.list is only a fallback: it seeds the defaults, a user
# override in ~/.config/mimeapps.list is written normally and wins per type.
{
  "text/html" = "firefox.desktop";
  "x-scheme-handler/about" = "firefox.desktop";
  "x-scheme-handler/http" = "firefox.desktop";
  "x-scheme-handler/https" = "firefox.desktop";
  "x-scheme-handler/unknown" = "firefox.desktop";
}
