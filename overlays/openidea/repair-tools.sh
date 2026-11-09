# Runs inside OpenIDEA's FHS environment, which supplies /lib64 and runtime libs.
app_data="${XDG_DATA_HOME:-$HOME/.local/share}/ai.l0ng.openidea"
tools_dir="${1:-$app_data/tools}"
log_file="${2:-$app_data/logs/nix-tools.log}"

if [[ ! -d "$tools_dir" ]]; then
  exit 0
fi

log() {
  mkdir -p -- "$(dirname -- "$log_file")"
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$log_file" >&2
}

# Do not follow symlinks: venv interpreters and Nix-managed tools belong to
# their original package manager. Only touch executable files owned by the IDE.
while IFS= read -r -d '' binary; do
  if ! interpreter="$(patchelf --print-interpreter "$binary" 2>/dev/null)"; then
    continue
  fi
  case "$interpreter" in
    /nix/store/*/lib/ld-linux-x86-64.so.2) ;;
    *) continue ;;
  esac
  if [[ -e "$interpreter" ]]; then
    continue
  fi

  # Replace atomically so an interruption cannot leave a truncated executable,
  # and an already running server can keep using its original inode.
  staging="$(mktemp -- "${binary}.openidea-nix.XXXXXX")"
  if cp --preserve=mode -- "$binary" "$staging" \
    && patchelf --set-interpreter /lib64/ld-linux-x86-64.so.2 "$staging" \
    && mv -f -- "$staging" "$binary"; then
    log "repaired $binary: $interpreter -> /lib64/ld-linux-x86-64.so.2"
  else
    rm -f -- "$staging"
    log "ERROR could not repair $binary (missing interpreter: $interpreter)"
    exit 1
  fi
done < <(find "$tools_dir" -type f -executable -print0)
