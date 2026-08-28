{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };

in
{
  programs.vscode = {
    enable = true;
    
    package = unstable-pkgs.vscode.override {
      commandLineArgs = [
        "--ozone-platform=wayland"
        "--enable-wayland-ime"
        "--wayland-text-input-version=3"
      ];
    };

    profiles.default.extensions = with pkgs.vscode-extensions; [
      ms-vscode.makefile-tools
      ms-vscode.cpptools-extension-pack
      ms-vscode.cpptools
      ms-vscode.cmake-tools
      ms-vscode-remote.vscode-remote-extensionpack
      ms-vscode-remote.remote-ssh-edit
      ms-vscode-remote.remote-ssh
      ms-vscode-remote.remote-containers
      vadimcn.vscode-lldb
      ms-python.python
      ms-python.pylint
      ms-python.vscode-pylance
      ms-python.debugpy
      platformio.platformio-vscode-ide
      ms-dotnettools.vscode-dotnet-runtime
      ms-dotnettools.csharp
      ms-dotnettools.csdevkit
      jnoortheen.nix-ide
      mesonbuild.mesonbuild
      mhutchie.git-graph
      donjayamanne.githistory
      shd101wyy.markdown-preview-enhanced
      alefragnani.project-manager
      github.github-vscode-theme
      pkief.material-icon-theme
    ];
  };
}

