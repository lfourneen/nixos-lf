# B's client DNS probe. The caller supplies a pool (or all IPv4 addresses for
# redir-host), a total budget, and ordered positive-name candidates.
client_dns() {
  local pool="$1" budget="$2"
  shift 2
  local deadline=$((SECONDS + budget)) round name answer remaining attempt
  client_dns_error='no answer yet'
  for round in 1 2 3; do
    for name in "$@"; do
      remaining=$((deadline - SECONDS))
      if [ "$remaining" -le 0 ]; then
        client_dns_error="client DNS deadline reached: $client_dns_error"
        return 1
      fi
      attempt=3
      if [ "$remaining" -lt "$attempt" ]; then attempt="$remaining"; fi
      if ! answer=$(timeout --signal=KILL "${attempt}s" "$DIG" +time="$attempt" +tries=1 \
          +short @127.0.0.1 -p "$DNS_PORT" "$name" A 2>/dev/null); then
        client_dns_error="dnsmasq did not answer $name within the probe deadline"
        continue
      fi
      if ! printf '%s\n' "$answer" | "$RULE_CHECK" address "$pool" >/dev/null; then
        client_dns_error="$name returned no address inside $pool"
        continue
      fi
      return 0
    done
    if [ "$round" -lt 3 ] && [ "$SECONDS" -lt "$deadline" ]; then sleep 1; fi
  done
  return 1
}
