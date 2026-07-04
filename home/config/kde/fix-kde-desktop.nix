{
  home.file.".local/bin/fix-kde-desktop.sh" = {
    text = ''
      #!/usr/bin/env bash

      DESKTOP_DIR="$HOME/Desktop"

      for f in "$DESKTOP_DIR"/*.desktop; do
          [ -e "$f" ] || continue

          if [ -L "$f" ] || [ ! -w "$f" ]; then
              echo "Fixing: $f"
              CONTENT=$(cat "$f")
              rm -f "$f"
              echo "$CONTENT" > "$f"
          fi

          chmod +x "$f"
          gio set "$f" metadata::trusted true 2>/dev/null || true
      done
    '';
    executable = true;
  };
}

