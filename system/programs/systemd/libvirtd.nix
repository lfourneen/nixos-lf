{ ... }:

{
  # Libvirt
  systemd.services.libvirtd.serviceConfig = {
    LoadCredential = "";
    LoadCredentialEncrypted = "";
  };
}

