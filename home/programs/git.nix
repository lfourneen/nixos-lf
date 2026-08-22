{ ... }:

{
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

