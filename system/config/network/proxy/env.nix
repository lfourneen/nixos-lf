{ lib }:

# Shared proxy endpoint and bypass primitives. Callers keep their own variable
# case and extra bypass entries: curl, Go, Python and Chromium do not parse
# NO_PROXY identically, so each consumer still renders its own environment.
let
  loopback = [ "127.0.0.1" "localhost" "::1" ];
  join = lib.concatStringsSep ",";

  # Clear both spellings wherever the service declines inherited proxy settings.
  unsetNames = [
    "http_proxy" "https_proxy" "ftp_proxy" "all_proxy"
    "HTTP_PROXY" "HTTPS_PROXY" "FTP_PROXY" "ALL_PROXY"
    "no_proxy" "NO_PROXY"
  ];

in
{
  inherit loopback join unsetNames;

  gostHttp = port: "http://127.0.0.1:${toString port}";
  gostHttpSlash = port: "http://127.0.0.1:${toString port}/";

  noProxy = { privateV4 ? [ ], extra ? [ ] }: join (loopback ++ privateV4 ++ extra);

  trustOverrideNames = unsetNames ++ [
    "NODE_USE_ENV_PROXY" "NODE_EXTRA_CA_CERTS" "SSL_CERT_FILE" "SSL_CERT_DIR"
    "CURL_CA_BUNDLE" "REQUESTS_CA_BUNDLE" "GIT_SSL_CAINFO"
  ];
}
