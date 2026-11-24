{ pkgs, lib, ... }:

let
  sources = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [ ./patches/patch_gost.py ./src/gost_http.go ./tests/gost_http_test.go ];
  };

in
{
  my.proxy.gostPackage = pkgs.gost.overrideAttrs (old: {
    doCheck = true;
    postConfigure = (old.postConfigure or "") + ''
      chmod -R u+w vendor/github.com/go-gost/x/handler/http
      ${pkgs.python3}/bin/python3 ${sources}/patches/patch_gost.py .
    '';
    checkPhase = ''
      runHook preCheck
      go test -mod=vendor github.com/go-gost/x/handler/http -run '^TestLF' -count=1
      runHook postCheck
    '';
  });
}
