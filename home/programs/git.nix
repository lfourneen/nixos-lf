{ ... }:

{
  # GIt
  programs.git = {
    enable = true;
    lfs.enable = true;

    settings = {
      init.defaultBranch = "main";
      
      user = {
        name  = "LFour86";
        email = "lfourneen@qq.com";
      };
    };
  };
}

