{ ... }:

{
  xdg.configFile."fuzzel/fuzzel.ini".text = ''
    [main]
    font=Maple Mono NF CN:size=13
    icons-enabled=yes
    horizontal-pad=50
    vertical-pad=25
    inner-pad=12
    width=50
    lines=12
    anchor=center

    [colors]
    background=0A0E14cc
    text=CBCCC6ff
    match=95E6CBff
    selection=1A212Eff
    selection-text=E6B450ff
    selection-match=E6B450ff
    border=E6B45088

    [border]
    width=2
    radius=10
  '';
}

