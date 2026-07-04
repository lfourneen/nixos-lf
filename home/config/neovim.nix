{ pkgs, ... }:

{
  programs.neovim = {
    enable = true;
    defaultEditor = true;
    viAlias = true;
    vimAlias = true;

    # Plugins
    plugins = with pkgs.vimPlugins; [
      neovim-ayu
      nvim-lspconfig
      nvim-cmp
      cmp-nvim-lsp
      lualine-nvim
    ];

    # External dependencies
    extraPackages = with pkgs; [
      ripgrep
      fd
      # LSP servers
      nixd
      clang-tools
      pyright
      typescript-language-server
      vscode-langservers-extracted
      rust-analyzer
      gopls
    ];

    # Main Configuration
    initLua = ''
      -- Basics
      vim.g.mapleader = " "
      vim.o.number = true
      vim.o.relativenumber = false
      vim.o.expandtab = true
      vim.o.shiftwidth = 2
      vim.o.tabstop = 2
      vim.o.smartindent = true
      vim.o.termguicolors = true
      vim.o.signcolumn = "yes"
      vim.o.updatetime = 300
      vim.o.clipboard = "unnamedplus"
      vim.deprecate = function() end

      -- Theme
      -- Requires 'neovim-ayu' plugin
      vim.cmd.colorscheme("ayu-dark")

      -- LSP Configuration
      local lspconfig = require("lspconfig")

      -- Common on_attach (avoids global keymap pollution)
      local on_attach = function(_, bufnr)
        local map = function(mode, lhs, rhs)
          vim.keymap.set(mode, lhs, rhs, { buffer = bufnr })
        end

        map("n", "gd", vim.lsp.buf.definition)
        map("n", "gr", vim.lsp.buf.references)
        map("n", "K", vim.lsp.buf.hover)
        map("n", "<leader>rn", vim.lsp.buf.rename)
        map("n", "<leader>ca", vim.lsp.buf.code_action)
      end

      -- LSP servers setup
      local servers = {
        nixd = {},
        clangd = {},
        pyright = {},
        ts_ls = {},
        eslint = {},
        rust_analyzer = {},
        gopls = {},
      }

      for name, cfg in pairs(servers) do
        lspconfig[name].setup(vim.tbl_extend("force", cfg, {
          on_attach = on_attach,
        }))
      end

      -- Diagnostic styling
      vim.diagnostic.config({
        virtual_text = false,
        signs = true,
        underline = true,
        update_in_insert = false,
      })

      -- Completion
      local cmp = require("cmp")
      cmp.setup({
        mapping = {
          ["<CR>"] = cmp.mapping.confirm({ select = true }),
          ["<C-Space>"] = cmp.mapping.complete(),
        },
        sources = {
          { name = "nvim_lsp" },
        },
      })

      -- Status Line
      require("lualine").setup({
        options = {
          section_separators = "",
          component_separators = "",
        },
      })
    '';
  };
}

