# The caller holds its snapshot lock throughout capture and comparison.
new_rules_snapshot() {
  local directory=$1 prefix=$2 path name
  local private="$directory/.ruleset"
  install -d -m 0700 "$private" || return 1
  for path in "$directory/$prefix".*.json; do
    name=${path##*/}
    if [[ $name =~ ^$prefix\.[0-9]+\.json$ ]]; then
      rm -f -- "$path" || return 1
    fi
  done
  rm -f -- "$private/$prefix".*.json || return 1
  SNAPSHOT=$(mktemp "$private/$prefix.XXXXXXXXXX.json")
}

remove_rules_snapshot() {
  if [ -n "$SNAPSHOT" ]; then
    rm -f -- "$SNAPSHOT"
    SNAPSHOT=""
  fi
}
