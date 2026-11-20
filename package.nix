{
  lib,
  libjxl,
  makeWrapper,
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "wallpaper-parallax-i3";
  inherit ((builtins.fromTOML (builtins.readFile ./pyproject.toml)).project) version;
  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./pyproject.toml
      ./src
    ];
  };

  pyproject = true;
  build-system = [ python3Packages.setuptools ];
  dependencies = with python3Packages; [
    pillow
    xlib
  ];
  nativeBuildInputs = [ makeWrapper ];
  makeWrapperArgs = [
    "--prefix"
    "PATH"
    ":"
    (lib.makeBinPath [ libjxl ])
  ];
  pythonImportsCheck = [ "wallpaper_parallax.daemon" ];

  meta = {
    description = "Android-style scrolling wallpaper for i3 on X11";
    homepage = "https://github.com/Kostr2007/Wallpaper-Parallax-i3";
    mainProgram = "wallpaper-parallax";
    platforms = lib.platforms.linux;
  };
}
