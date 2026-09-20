{ lib
, rustPlatform
, fetchFromGitHub
, pkg-config
, glslang
, wayland
, vulkan-headers
, vulkan-loader
, libGL
, gettext
, writeTextDir
, makeWrapper
}:

let
  # nixpkgs' vulkan-headers ships the headers but no `vulkan.pc`, and the
  # upstream build.rs hard-requires a pkg-config `vulkan` module (include
  # paths only, resolved via dlopen at runtime). Provide a minimal shim.
  vulkanPc = writeTextDir "lib/pkgconfig/vulkan.pc" ''
    prefix=${vulkan-headers}
    Name: Vulkan-Headers
    Description: Vulkan header files
    Version: ${vulkan-headers.version}
    Cflags: -I${vulkan-headers}/include
  '';

in
rustPlatform.buildRustPackage rec {
  pname = "waywallen-layer-shell";
  version = "0.3.3";

  src = fetchFromGitHub {
    owner = "waywallen";
    repo = "waywallen-display";
    rev = "v${version}";
    sha256 = "0bwlnhvs8vs1lkianmj3lfgsk3mjadmyrsmqhahcg55qx7xwi86f";
  };

  cargoHash = "sha256-TEVUvyX5eoZYe109ufTMb5hOHAiqabUlouvHn5x9kbo=";

  nativeBuildInputs = [ pkg-config glslang makeWrapper ];
  buildInputs = [ wayland vulkan-headers vulkan-loader libGL gettext ];

  preBuild = ''
    export PKG_CONFIG_PATH="${vulkanPc}/lib/pkgconfig:$PKG_CONFIG_PATH"
  '';

  cargoBuildFlags = [ "--bin" "waywallen-layer-shell" ];

  # libvulkan / libEGL / libGLESv2 are dlopen()ed at runtime, so they are not
  # recorded as DT_NEEDED; put them on LD_LIBRARY_PATH explicitly.
  postFixup = ''
    wrapProgram $out/bin/waywallen-layer-shell \
      --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath [ vulkan-loader libGL ]}"
  '';

  meta = {
    description = "Wayland layer-shell wallpaper client for the waywallen daemon";
    homepage = "https://github.com/waywallen/waywallen-display";
    license = lib.licenses.mit;
    mainProgram = "waywallen-layer-shell";
    platforms = lib.platforms.linux;
  };
}

