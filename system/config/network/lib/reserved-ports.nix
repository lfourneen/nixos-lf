{ lib, ports }:

lib.unique (map toString [
  ports.mihomoMixed ports.mihomoStrict ports.mihomoDns ports.mihomoTproxy
  ports.gostHttp ports.gostRedirect ports.dnsmasq ports.unbound ports.socketGuestDns
  ports.coreDnsTransport ports.coreDnsBootstrapTransport ports.coreLoopback ports.coreRealDns
  ports.unboundInfrastructure ports.coreInfrastructureDns
])
