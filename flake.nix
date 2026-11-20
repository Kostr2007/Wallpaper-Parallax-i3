{
  description = "Android-style scrolling wallpaper for i3 on X11";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forSystems (system: {
        default = nixpkgs.legacyPackages.${system}.callPackage ./package.nix { };
      });

      checks = forSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python3.withPackages (ps: [
            ps.pillow
            ps.xlib
          ]);
        in
        {
          x11 =
            pkgs.runCommand "wallpaper-parallax-x11-test"
              {
                nativeBuildInputs = [
                  python
                  pkgs.i3
                  pkgs.xvfb
                  (pkgs.lib.getBin pkgs.libjxl)
                  self.packages.${system}.default
                ];
              }
              ''
                python ${./tests/x11_smoke.py}
                touch "$out"
              '';
        }
      );

      homeManagerModules.default = import ./home-manager.nix;
    };
}
