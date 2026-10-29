{
  description = "ARLO - Arc Raiders Loot Overlay (dev environment for NixOS)";

  inputs.nixpkgs.url = "nixpkgs";

  outputs = { self, nixpkgs }:
    let
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
      # Kernel headers and compiler for building the evdev Python package (pynput dependency)
      # X11 libs so mss (X11 backend) finds libX11/libXfixes/libXrandr at runtime
      # glib/cairo so uv can build PyGObject; GStreamer + pipewire for Wayland capture
      # Same nixpkgs Python as glib/GStreamer. A uv-downloaded CPython is
      # patched to whatever glibc was current at install time, then breaks
      # after `nix flake update` (GLIBC_2.xx not found when importing gi).
      # nixpkgs Python omits Tk; the overlay imports tkinter at startup.
      pythonTkinter = pkgs.python313Packages.tkinter;
      buildInputs = with pkgs; [
        uv
        python313
        pythonTkinter
        tesseract
        linuxHeaders
        gcc
        pkg-config
        libx11
        libxfixes
        libxrandr
        glib
        libffi
        cairo
        gobject-introspection
        gst_all_1.gstreamer
        gst_all_1.gst-plugins-base
        pipewire
      ];
      libPath = pkgs.lib.makeLibraryPath (with pkgs; [
        libx11
        libxfixes
        libxrandr
        glib
        cairo
        stdenv.cc.cc.lib
      ]);
      giTypelibPath = pkgs.lib.makeSearchPath "lib/girepository-1.0" (with pkgs; [
        glib.out
        gobject-introspection
        gst_all_1.gstreamer
        gst_all_1.gst-plugins-base
      ]);
      gstPluginPath = pkgs.lib.makeSearchPath "lib/gstreamer-1.0" (with pkgs; [
        gst_all_1.gstreamer
        gst_all_1.gst-plugins-base
        pipewire
      ]);
    in
    {
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = buildInputs;
        C_INCLUDE_PATH = "${pkgs.linuxHeaders}/include";
        CPATH = "${pkgs.linuxHeaders}/include";
        LD_LIBRARY_PATH = libPath;
        GI_TYPELIB_PATH = giTypelibPath;
        GST_PLUGIN_SYSTEM_PATH_1_0 = gstPluginPath;
        UV_PYTHON = "${pkgs.python313}/bin/python3";
        UV_PYTHON_DOWNLOADS = "never";
        PYTHONPATH = "${pythonTkinter}/${pkgs.python313.sitePackages}";
      };
    };
}
