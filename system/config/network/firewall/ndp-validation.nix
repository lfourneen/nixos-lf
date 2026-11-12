# Shared ingress validation, before conntrack accepts, for both boot and the
# complete ruleset. The kernel still validates lengths, options and checksum.
''
  meta l4proto icmpv6 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert } ip6 hoplimit != 255 counter drop
  meta l4proto icmpv6 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert } icmpv6 code != 0 counter drop
  meta l4proto icmpv6 icmpv6 type nd-router-advert ip6 saddr != fe80::/10 counter drop
  meta l4proto icmpv6 icmpv6 type nd-router-advert ip6 daddr ff00::/8 ip6 daddr != ff02::1 counter drop
  meta l4proto icmpv6 icmpv6 type nd-router-solicit ip6 saddr != { fe80::/10, ::/128 } counter drop
  meta l4proto icmpv6 icmpv6 type nd-router-solicit ip6 daddr ff00::/8 ip6 daddr != ff02::2 counter drop
  meta l4proto icmpv6 icmpv6 type nd-redirect counter drop
''
