{ config, ... }:

{
  # Some programs need SUID wrappers, can be configured further or are
  # started in user sessions.
   programs.mtr.enable = true;
   programs.gnupg.agent = {
     enable = true;
     enableSSHSupport = true;
  };

  # Enable the OpenSSH daemon. DEPENDS ON:
  #  - network.nix: uncomment the LAN/tailnet `tcp dport 22` firewall rules
  #  - secrets.yaml already holds ssh_host_ed25519_key + ssh_authorized_keys
  #    (add/rotate with: sops set system/programs/secrets/secrets.yaml).
  #    btrfs-rollback wipes /etc every boot, so host keys MUST come from sops
  #    (rendered into /run/secrets by sops-install-secrets in sysinit) —
  #    keep the "/etc/ssh" impermanence entries in partition.nix commented.
  #
  # ---- KEY REGENERATION (short steps) ----
  #  1. Client keypair:  ssh-keygen -t ed25519 -C "lfour@nixos" -f ~/.ssh/id_ed25519
  #  2. Host key:        ssh-keygen -t ed25519 -N "" -C "ssh_host_ed25519_key" -f /tmp/ssh_host_ed25519_key
  #  3. Into sops:       sops system/programs/secrets/secrets.yaml
  #      -> paste /tmp/ssh_host_ed25519_key into  ssh_host_ed25519_key
  #      -> paste ~/.ssh/id_ed25519.pub into     ssh_authorized_keys
  #  4. Rebuild:         sudo nixos-rebuild switch --flake .#lfour
  #  5. Clients:         ssh-keygen -R <host> ; connect with the NEW private key
  #  NOTE: NEVER touch ~/.config/sops/age/keys.txt here - it decrypts ALL secrets.
  services.openssh = {
    enable = true;
    ports = [ 22 ];
    # Ed25519 host key from sops (modern only; add a rsa/ecdsa entry here if
    # you must support ancient clients)
    hostKeys = [
      { path = config.sops.secrets."ssh_host_ed25519_key".path; type = "ed25519"; }
    ];
    settings = {
      # Key-based auth ONLY (authorized keys come from sops)
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
      PermitEmptyPasswords = false;

      PermitRootLogin = "no";
      AllowUsers = [ "lfour" ];
      AuthorizedKeysFile = ".ssh/authorized_keys ${config.sops.secrets."ssh_authorized_keys".path}";

      X11Forwarding = false;
      AllowAgentForwarding = false;
      AllowTcpForwarding = false;   # re-enable if you need ssh -L/-R tunnels
      MaxAuthTries = 3;
      LoginGraceTime = 30;
      UseDns = false;
    };
  };

  # sops-nix secrets (common sops options: hermes.nix). Rendered in sysinit,
  # before sshd starts (/run/secrets/*).
  sops.secrets = {
    ssh_host_ed25519_key = {
      owner = "root";
      group = "sshd";
      mode = "0640";
    };
    ssh_authorized_keys = {
      owner = "root";
      group = "root";
      mode = "0600";
    };
  };
}

