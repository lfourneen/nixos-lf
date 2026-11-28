{ ... }:

{
  # Keep the Noctalia theme (colors) but let the wallpaper show through the
  # terminal background. The niri window rule below keeps text at full opacity.
  xdg.configFile."kitty/kitty.conf".text = ''
    include themes/noctalia.conf

    background_opacity 0.0
  '';
}
