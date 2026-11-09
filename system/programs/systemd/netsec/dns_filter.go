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
		return next(ctx, request)
	}
}
