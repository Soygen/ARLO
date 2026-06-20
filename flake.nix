{
  description = "ARLO - Arc Raiders Loot Overlay (dev environment for NixOS)";

  inputs.nixpkgs.url = "nixpkgs";

  outputs = { self, nixpkgs }:
    let
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
      # Kernel headers and compiler for building the evdev Python package (pynput dependency)
      # X11 libs so mss (X11 backend) finds libX11/libXfixes/libXrandr at runtime
      # glib/cairo so uv can build PyGObject; GStreamer + pipewire for Wayland capture
      buildInputs = with pkgs; [
        uv
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
      };
    };
}
