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
  users.users.clash-validate = {
    isSystemUser = true;
    uid = config.my.machine.uids.clashValidate;
    group = "clash-core";
    description = "Capability-free proxy configuration validation";
  };

  assertions = [{
    assertion = uid > 0 && config.users.users.clash-core.uid == uid
      && lib.count (u: u.uid == uid) (lib.attrValues config.users.users) == 1;
    message = "The Clash core requires its own non-root, pinned UID.";
  } {
    assertion = config.my.machine.uids.clashValidate > 0
      && lib.count (u: u.uid == config.my.machine.uids.clashValidate) (lib.attrValues config.users.users) == 1;
    message = "The proxy validator requires a fixed UID separate from all live transports.";
  }];
}
