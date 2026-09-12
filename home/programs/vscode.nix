{ pkgs, ... }:

{
  programs.vscode = {
    enable = true;
    
    package = pkgs.unstable.vscode.override {
      commandLineArgs = [
        "--ozone-platform=wayland"
        "--enable-wayland-ime"
        "--wayland-text-input-version=3"
      ];
    };

    profiles.default.extensions = with pkgs.vscode-extensions; [
      # C/C++ & Build
      mesonbuild.mesonbuild
      ms-vscode.cmake-tools
      ms-vscode.cpptools
      ms-vscode.cpptools-extension-pack
      ms-vscode.makefile-tools
      vadimcn.vscode-lldb

      # Remote
      ms-vscode-remote.remote-containers
      ms-vscode-remote.remote-ssh
      ms-vscode-remote.remote-ssh-edit
      ms-vscode-remote.vscode-remote-extensionpack

      # Python
      ms-python.debugpy
      ms-python.pylint
      ms-python.python
      ms-python.vscode-pylance

      # .NET
      ms-dotnettools.csdevkit
      ms-dotnettools.csharp
      ms-dotnettools.vscode-dotnet-runtime

      # Embedded
      platformio.platformio-vscode-ide

      # Nix
      jnoortheen.nix-ide

      # LaTeX
      james-yu.latex-workshop

      # Git
      donjayamanne.githistory
      mhutchie.git-graph

      # Markdown
      shd101wyy.markdown-preview-enhanced

      # Project Manager
      alefragnani.project-manager

      # Themes & Icons
      github.github-vscode-theme
      pkief.material-icon-theme
    ];
  };
}

