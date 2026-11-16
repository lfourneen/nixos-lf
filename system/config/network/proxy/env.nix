{ lib }:

# Shared proxy endpoint and bypass primitives. Callers keep their own variable
# case and extra bypass entries: curl, Go, Python and Chromium do not parse
# NO_PROXY identically, so each consumer still renders its own environment.
let
  loopback = [ "127.0.0.1" "localhost" "::1" ];
  join = lib.concatStringsSep ",";

in
{
  inherit loopback join;

  gostHttp = port: "http://127.0.0.1:${toString port}";
  gostHttpSlash = port: "http://127.0.0.1:${toString port}/";

  noProxy = { privateV4 ? [ ], extra ? [ ] }: join (loopback ++ privateV4 ++ extra);

  # Every spelling a client may read. Clearing the whole set avoids leaving an
  # inherited proxy active behind a differently-cased variable.
  unsetNames = [
    "http_proxy" "https_proxy" "ftp_proxy" "all_proxy"
    "HTTP_PROXY" "HTTPS_PROXY" "FTP_PROXY" "ALL_PROXY"
    "no_proxy" "NO_PROXY"
  ];
}
