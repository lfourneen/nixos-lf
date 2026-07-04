{ ... }:
{
  xdg.configFile."nushell/config.nu".text = ''
    # Set theme
    let ayu_dark_theme = {
      separator: "#5c6773"
      leading_trailing_space_bg: { attr: "n" }
      header: { fg: "#ffb454" attr: "b" }
      empty: "#59c2ff"
      bool: "#ffb454"
      int: "#ffb454"
      float: "#ffb454"
      filesize: "#ffb454"
      duration: "#ffb454"
      date: "#ffb454"
      range: "#ffb454"
      string: "#91b362"
      binary: "#91b362"
      insert_mode: { fg: "#91b362" attr: "b" }
      replace_mode: { fg: "#ffb454" attr: "b" }
      selected_row: { fg: "#31363f" bg: "#ffb454" }
      record: "#ffb454"
      list: "#ffb454"
      block: "#ffb454"
      hints: "#5c6773"
      search_result: { fg: "#ffb454" bg: "#5c6773" }
      shape_and: "#f07178"
      shape_binary: "#f29718"
      shape_block: "#59c2ff"
      shape_bool: "#ffb454"
      shape_custom: "#91b362"
      shape_datetime: "#ffb454"
      shape_directory: "#59c2ff"
      shape_external: "#91b362"
      shape_externalarg: "#91b362"
      shape_filepath: "#59c2ff"
      shape_flag: "#f07178"
      shape_float: "#ffb454"
      shape_garbage: { fg: "#ffffff" bg: "#ff3333" attr: "b" }
      shape_globpattern: "#59c2ff"
      shape_int: "#ffb454"
      shape_internalcall: "#59c2ff"
      shape_list: "#59c2ff"
      shape_literal: "#59c2ff"
      shape_match_pattern: "#91b362"
      shape_matching_brackets: { attr: "u" }
      shape_nothing: "#ffb454"
      shape_operator: "#f29718"
      shape_or: "#f07178"
      shape_pipe: "#f29718"
      shape_range: "#f29718"
      shape_record: "#59c2ff"
      shape_redirection: "#f29718"
      shape_signature: "#91b362"
      shape_string: "#91b362"
      shape_string_interpolation: "#59c2ff"
      shape_table: "#59c2ff"
      shape_variable: "#ffb454"
    }

    $env.config = {
      show_banner: false
      color_config: $ayu_dark_theme
    }

    $env.PATH = ($env.PATH | split row (char esep) | prepend ($env.HOME | path join ".local/bin"))
    $env.EDITOR = "nvim"
    $env.VISUAL = "nvim"
    $env.PROMPT_COMMAND_RIGHT = { "" }

    $env.PROMPT_COMMAND = { ||
      let user = ($env.USER? | default "user")
      let host = (hostname)
      let path = ($env.PWD | str replace $env.HOME "~")
      let ayu_yellow = (ansi { fg: "#ffb454" })
      let ayu_red = (ansi { fg: "#f07178" })
      let ayu_green = (ansi { fg: "#91b362" })
      let reset = (ansi reset)
      $"($ayu_yellow)[($user)@($host)|($path)]-($ayu_red)>($ayu_green)>($reset)"
    }

    # Update config
    def update [--flake: string = "/etc/nixos#lfour"] {
      print $"(ansi yellow_bold)Rebuilding system with nh...(ansi reset)"
      sudo nh os switch --ask --bypass-root-check $flake
      let exit_code = ($env.LAST_EXIT_CODE | into string)
      if $exit_code == "0" {
        print $"(ansi green_bold)System updated successfully ✓(ansi reset)"
      } else {
        print $"(ansi red_bold)Update failed [exit code: ($exit_code)] ✗(ansi reset)"
      }
    }

    # Upgrade system
    def upgrade [--flake: string = "/etc/nixos#lfour"] {
      print $"(ansi yellow_bold)Starting system upgrade...(ansi reset)"
      
      let flake_dir = ($flake | split row "#" | get 0)
      print $"(ansi yellow)Updating flake inputs in ($flake_dir)...(ansi reset)"
      sudo nix flake update --flake $flake_dir
      let update_exit = ($env.LAST_EXIT_CODE | into string)
      
      if $update_exit != "0" {
        print $"(ansi red_bold)Flake update failed [exit code: ($update_exit)] ✗(ansi reset)"
        return
      }
      
      print $"(ansi yellow)Rebuilding system with nh...(ansi reset)"
      sudo nh os switch --ask --bypass-root-check $flake
      let switch_exit = ($env.LAST_EXIT_CODE | into string)
      
      if $switch_exit == "0" {
        print $"(ansi green_bold)Upgrade completed successfully ✓(ansi reset)"
      } else {
        print $"(ansi red_bold)Upgrade failed [exit code: ($switch_exit)] ✗(ansi reset)"
      }
    }

    # Update flake
    def flake [--dir: string = "/etc/nixos"] {
      print $"(ansi yellow)Updating flake in ($dir)...(ansi reset)"
      sudo nix flake update --flake $dir
      let exit_code = ($env.LAST_EXIT_CODE | into string)
      if $exit_code == "0" {
        print $"(ansi green_bold)Flake updated successfully ✓(ansi reset)"
      } else {
        print $"(ansi red_bold)Flake update failed [exit code: ($exit_code)] ✗(ansi reset)"
      }
    }

    def fix [] {
      sudo nix-store --verify --check-contents --repair
    }

    def garbage [] {
      nh clean all --ask
    }

    def g3d [--days: int = 3] {
      let age = ($days | into string) + "d"
      sudo nix-collect-garbage --delete-older-than $age
      nix-collect-garbage --delete-older-than $age
    }

    # Fetch the PAC file and set the environment variables
    def --env proxy-on [] {
      let pac_url = "http://127.0.0.1:33331/commands/pac"
      let content = (try {
        http get --max-time 1sec $pac_url
      } catch {
        ""
      })
      if ($content | is-empty) == false {
        let parsed = ($content | parse --regex 'PROXY\s+(?<addr>[\d\.]+:\d+)')
        if ($parsed | is-empty) == false {
          let proxy_addr = ($parsed | get 0.addr)
          let proxy_url = $"http://($proxy_addr)"
          $env.http_proxy = $proxy_url
          $env.https_proxy = $proxy_url
        }
      }
    }
    
    # Clear the proxy environment variables
    def --env proxy-off [] {
      hide-env http_proxy
      hide-env https_proxy
      print $"(ansi yellow)Proxy disabled.(ansi reset)"
    }

    # Autostart proxy
    proxy-on
  '';
}

