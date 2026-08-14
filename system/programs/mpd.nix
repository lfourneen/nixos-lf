{ ... }:

{
  # Music Player Daemon
  services.mpd = {
    enable = true;
    user = "lfour";
    startWhenNeeded = true;
    openFirewall = false;
    settings = {
      music_directory = "/home/lfour/Music";
      bind_to_address = "127.0.0.1";
      audio_output = [
        {
          type = "pipewire";
          name = "PipeWire Output";
        }
        {
          type = "fifo";
          name = "FIFO";
          path = "/tmp/mpd.fifo";
          format = "44100:16:2";
        }
      ];
    };
  };
  systemd.services.mpd.environment = {
    XDG_RUNTIME_DIR = "/run/user/1000";
  };
}