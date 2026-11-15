package dns

import (
	"github.com/metacubex/mihomo/component/lfguard"
	icontext "github.com/metacubex/mihomo/context"
	D "github.com/miekg/dns"
)

func lfServiceRecords() bool { return lfguard.Managed() }

func lfBlockedReply(request *D.Msg) *D.Msg {
	for _, question := range request.Question {
		if lfguard.CheckDomain(question.Name) != nil {
			reply := new(D.Msg)
			reply.SetRcode(request, D.RcodeNameError)
			reply.RecursionAvailable = true
			return reply
		}
	}
	return nil
}

func lfDomainFilter(next handler) handler {
	return func(ctx *icontext.DNSContext, request *D.Msg) (*D.Msg, error) {
		if reply := lfBlockedReply(request); reply != nil {
			return reply, nil
		}
		if lfguard.Managed() && len(request.Question) != 1 {
			return new(D.Msg).SetRcode(request, D.RcodeFormatError), nil
		}
		// Validate before hosts/cache/fake-IP can turn a bogus name into a
		// synthetic success. Negative and failed verdicts are returned intact.
		var validated *D.Msg
		if len(request.Question) == 1 {
			kind := request.Question[0].Qtype
			if kind == D.TypeA || kind == D.TypeAAAA {
				var err error
				validated, err = lfguard.ValidateDNS(ctx, request)
				if err != nil {
					return nil, err
				}
				if validated != nil {
					hasAddress := false
					for _, record := range validated.Answer {
						if record.Header().Rrtype == kind {
							hasAddress = true
							break
						}
					}
					// A signed CNAME chain can end in NODATA. Its alias
					// records alone cannot authorize a synthetic address.
					if validated.Rcode != D.RcodeSuccess || !hasAddress {
						return validated, nil
					}
				}
			}
		}
		reply, err := next(ctx, request)
		if reply != nil && ctx.Type() == icontext.DNSTypeFakeIP {
			// Fake addresses are local mappings, not signed address records.
			reply.AuthenticatedData = false
		}
		return reply, err
	}
}
