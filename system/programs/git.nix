{ ... }:

{
  # GIt
  programs.git = {
    enable = true;
    lfs.enable = true;

    config = {
      init.defaultBranch = "main";

      user = {
        name  = "LFour86";
        email = "lfourneen@qq.com";
      };

      # Use gh's credentials for GitHub (gh auth login already done).
      # Written declaratively to /etc/gitconfig by this NixOS module.
      credential = {
        helper = "";
      };
      "credential \"https://github.com\"" = {
        helper = "!gh auth git-credential";
      };
    };
  };
}

