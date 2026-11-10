{ config, lib, ... }:

let
  uid = config.my.machine.uids.clashCore;

in
{
  users.groups.clash-core = { };
  users.users.clash-core = {
    inherit uid;
    isSystemUser = true;
    group = "clash-core";
    description = "Managed Clash core";
    home = "/var/lib/clash-core";
  };

  assertions = [{
    assertion = uid > 0 && config.users.users.clash-core.uid == uid
      && lib.count (u: u.uid == uid) (lib.attrValues config.users.users) == 1;
    message = "The Clash core requires its own non-root, pinned UID.";
  }];
}
