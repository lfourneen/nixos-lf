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
      ms-python.debugpy
      ms-python.pylint
      ms-python.vscode-pylance
      platformio.platformio-vscode-ide
      ms-dotnettools.vscode-dotnet-runtime
      ms-dotnettools.csharp
      ms-dotnettools.csdevkit
      jnoortheen.nix-ide
      mesonbuild.mesonbuild
      pkief.material-icon-theme
      mhutchie.git-graph
      donjayamanne.githistory
      shd101wyy.markdown-preview-enhanced
    ];
  };

  programs.zed-editor = {
    package = unstable-pkgs.zed-editor;
    enable = true;
    extensions = [ 
      "nix"
    ];
  };

  # Notice
  services.mako = {
    enable = false;
    settings = {
      anchor = "top-right";
      width =350;
      default-timeout = 5000; # ms
      background-color="#FBD0B5";
      text-color="#5C4A1D";
      border-color="#272939";
      progress-color="over #5FA8A0";
      border-size = 2;
      border-radius = 8;
      padding = 12;
      font = "Maple Mono NF 10";
    };
  };

  # GIt
  programs.git = {
    enable = true;
    lfs.enable = true;
    settings = {
      user = {
        name  = "LFour86";
        email = "LFour86@example.com";
      };
      init.defaultBranch = "main";
    };
  };
}

