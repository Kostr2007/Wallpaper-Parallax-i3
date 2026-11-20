{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.wallpaper-parallax;
  launcher = pkgs.writeShellScript "start-wallpaper-parallax" ''
    exec ${lib.getExe cfg.package} ${
      lib.escapeShellArgs (
        [
          "--pages"
          (toString cfg.pages)
          "--travel"
          (toString cfg.travel)
          "--duration"
          (toString cfg.duration)
          "--fps"
          (toString cfg.fps)
        ]
        ++ lib.optionals (cfg.image != null) [
          "--image"
          (toString cfg.image)
        ]
        ++ lib.optionals (cfg.stateFile != null) [
          "--state-file"
          cfg.stateFile
        ]
      )
    }
  '';
in
{
  options.services.wallpaper-parallax = {
    enable = lib.mkEnableOption "animated wallpaper in i3 on X11";
    package = lib.mkPackageOption {
      wallpaper-parallax = pkgs.callPackage ./package.nix { };
    } "wallpaper-parallax" { };

    image = lib.mkOption {
      type = lib.types.nullOr (lib.types.either lib.types.path lib.types.str);
      default = null;
      example = "/home/alex/Pictures/panorama.jpg";
      description = "Path to a fixed wallpaper image.";
    };

    stateFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Path to a JSON file with the current wallpaper path and fit.";
    };

    sessionTarget = lib.mkOption {
      type = lib.types.str;
      default = "i3-session.target";
      description = "User systemd target that starts and stops with the i3 session.";
    };

    pages = lib.mkOption {
      type = lib.types.ints.between 2 1000;
      default = 5;
      description = "Number of numbered workspaces mapped across the panorama.";
    };

    travel = lib.mkOption {
      type = lib.types.ints.between 0 100;
      default = 20;
      description = "Total horizontal travel as a percentage of an output's width.";
    };

    duration = lib.mkOption {
      type = lib.types.ints.between 0 5000;
      default = 320;
      description = "Transition duration in milliseconds.";
    };

    fps = lib.mkOption {
      type = lib.types.ints.between 1 144;
      default = 60;
      description = "Animation frame rate limit.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = (cfg.image == null) != (cfg.stateFile == null);
        message = "Set exactly one of services.wallpaper-parallax.image and .stateFile.";
      }
    ];

    home.packages = [ cfg.package ];

    systemd.user.services.wallpaper-parallax = {
      Unit = {
        Description = "i3 wallpaper parallax";
        PartOf = [ cfg.sessionTarget ];
        ConditionEnvironment = "DISPLAY";
      };
      Service = {
        ExecStart = toString launcher;
        Restart = "on-failure";
        RestartSec = 2;
      };
      Install.WantedBy = [ cfg.sessionTarget ];
    };
  };
}
