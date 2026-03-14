{
  description = "ARLO - Arc Raiders Loot Overlay (dev environment for NixOS)";

  inputs.nixpkgs.url = "nixpkgs";

  outputs = { self, nixpkgs }:
    let
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
      # Kernel headers and compiler for building the evdev Python package (pynput dependency)
      # X11 libs so mss (screen capture) can find libX11, libXfixes, libXrandr at runtime
      buildInputs = with pkgs; [
        uv
        tesseract
        linuxHeaders
        gcc
        libx11
        libxfixes
        libxrandr
      ];
      # So ctypes.find_library() and the dynamic linker find X11 libs
      libPath = pkgs.lib.makeLibraryPath (with pkgs; [
        libx11
        libxfixes
        libxrandr
      ]);
    in
    {
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = buildInputs;
        # Help evdev's setup.py find kernel headers when building via uv/pip
        C_INCLUDE_PATH = "${pkgs.linuxHeaders}/include";
        CPATH = "${pkgs.linuxHeaders}/include";
        # So mss (screen capture) can load X11 libraries
        LD_LIBRARY_PATH = libPath;
      };
    };
}
