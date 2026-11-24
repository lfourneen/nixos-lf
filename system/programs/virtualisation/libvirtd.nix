{ pkgs, ... }:

{
  programs.virt-manager.enable = true;
  virtualisation.spiceUSBRedirection.enable = true;

  virtualisation.libvirtd = {
    enable = true;
    qemu = {
      package = pkgs.qemu_kvm;
      runAsRoot = false;
      swtpm.enable = true;
      vhostUserPackages = [ pkgs.virtiofsd ];
    };
  };

  # libvirt's packaged unit loads the secrets-encryption-key credential from
  # /var/lib/libvirt/secrets. This host does not use encrypted libvirt secrets,
  # so both credential lists are cleared and libvirtd does not depend on the key.
  systemd.services.libvirtd.serviceConfig = {
    LoadCredential = "";
    LoadCredentialEncrypted = "";
  };
}
