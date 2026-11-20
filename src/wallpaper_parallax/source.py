"""Load an image or a layered wallpaper and follow the desktop state file."""

from dataclasses import dataclass, replace
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageOps

from .motion import MODES, MotionSettings

LAYER_NAMES = ("background", "atmosphere", "middle", "foreground", "particles")
IMAGE_ERRORS = (OSError, ValueError, subprocess.SubprocessError, Image.DecompressionBombError)


@dataclass(frozen=True)
class Ambient:
    period: float
    layers: dict[str, tuple[float, float, float]]


@dataclass(frozen=True)
class Wallpaper:
    preview: Image.Image
    layers: tuple[tuple[str, Image.Image], ...] = ()
    ambient: Ambient | None = None


def file_identity(path):
    if path is not None:
        try:
            stat = path.stat()
            return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size
        except OSError:
            pass
    return None


def decode(path, mode):
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original)
        if mode == "RGB" and ("A" in image.getbands() or "transparency" in image.info):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size)
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return image.convert(mode)


def load_rgb(path):
    if path.suffix.lower() != ".jxl" or not (decoder := shutil.which("djxl")):
        return decode(path, "RGB")
    with tempfile.TemporaryDirectory() as directory:
        png = Path(directory) / "wallpaper.png"
        subprocess.run(
            [decoder, str(path.resolve()), str(png)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
        )
        return decode(png, "RGB")


def layer_files(path):
    if path is None:
        return ()
    directory = path.with_suffix(".parallax")
    return tuple((name, directory / f"{name}.png") for name in LAYER_NAMES)


def load_ambient(manifest, layers):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("expected motion manifest version 1")
    period = data.get("period")
    if type(period) not in (int, float) or not 2 <= period <= 120:
        raise ValueError("period must be between 2 and 120 seconds")
    planes = data.get("layers")
    if not isinstance(planes, dict) or not planes:
        raise ValueError("expected at least one moving layer")
    names = {name for name, _ in layers}
    for name, values in planes.items():
        if name not in names or not isinstance(values, list) or len(values) != 3:
            raise ValueError(f"expected [x, y, phase] for an existing layer, got {name!r}")
        if any(type(value) not in (int, float) for value in values):
            raise ValueError(f"expected numeric motion for {name!r}")
        x, y, phase = values
        if not (0 <= x <= 5 and 0 <= y <= 5 and 0 <= phase <= 1):
            raise ValueError("x and y must be in 0–5 percent, phase in 0–1")
    return Ambient(period, {name: tuple(values) for name, values in planes.items()})


def load_wallpaper(path, layers, manifest, warn):
    preview = load_rgb(path)
    if not layers:
        return Wallpaper(preview)
    try:
        images = tuple((name, decode(file, "RGB" if name == "background" else "RGBA"))
                       for name, file in layers)
        if any(image.size != preview.size for _, image in images):
            raise ValueError("layer sizes must match the flat wallpaper")
    except IMAGE_ERRORS as exc:
        warn(f"{path.with_suffix('.parallax')}: {exc}")
        return Wallpaper(preview)
    ambient = None
    if manifest is not None:
        try:
            ambient = load_ambient(manifest, images)
        except (OSError, ValueError) as exc:
            warn(f"{manifest}: {exc}")
    return Wallpaper(preview, images, ambient)


def percentage(data, key, default):
    value = data.get(key, default)
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError(f"{key} must be an integer between 0 and 100")
    return value


class Source:
    def __init__(self, image, state_file, motion, depth, warn, *, ambient=True):
        self.path = image
        self.state_file = state_file
        self.state_identity = None
        self.image_identity = None
        self.wallpaper = None
        self.fit = "fill"
        self.defaults = motion, depth
        self.motion, self.depth = self.defaults
        self.ambient = self.ambient_default = ambient
        if image is not None:
            self.refresh_image(warn, strict=True)

    def refresh(self, warn):
        state_identity = file_identity(self.state_file)
        if state_identity != self.state_identity:
            self.state_identity = state_identity
            self.path = None
            self.motion, self.depth = self.defaults
            self.ambient = self.ambient_default
            if state_identity is not None:
                try:
                    self.read_state(warn)
                except (OSError, TypeError, KeyError, ValueError) as exc:
                    warn(f"{self.state_file}: {exc}")

        return self.refresh_image(warn)

    def read_state(self, warn):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        image = Path(state["image"])
        if not image.is_absolute():
            raise ValueError("image path must be absolute")
        fit = state.get("fit", "fill")
        if fit not in ("fill", "center", "max", "tile"):
            raise ValueError(f"unknown fit mode {fit!r}")
        self.fit = fit
        self.path = image

        try:
            mode = state.get("motion", self.motion.mode)
            scale = state.get("duration_scale", 1)
            if mode not in MODES:
                raise ValueError(f"unknown motion mode {mode!r}")
            if type(scale) not in (int, float) or not 0.5 <= scale <= 2:
                raise ValueError("duration_scale must be between 0.5 and 2")
            inertia = percentage(state, "inertia", self.motion.inertia)
            depth = percentage(state, "depth", self.depth)
            ambient = state.get("ambient", True)
            if type(ambient) is not bool:
                raise ValueError("ambient must be a boolean")
            self.motion = replace(self.motion, mode=mode, duration=self.motion.duration * scale, inertia=inertia)
            self.depth = depth
            self.ambient = self.ambient_default and ambient
        except ValueError as exc:
            warn(f"{self.state_file}: {exc}")

    def refresh_image(self, warn, strict=False):
        path = self.path if self.fit == "fill" else None
        signature = tuple((name, file, file_identity(file)) for name, file in layer_files(path))
        manifest = path.with_suffix(".parallax") / "motion.json" if path is not None else None
        manifest_identity = file_identity(manifest)
        identity = path, file_identity(path), signature, manifest_identity
        if identity == self.image_identity:
            return False

        self.image_identity = identity
        self.wallpaper = None
        if path is not None:
            layers = tuple((name, file) for name, file, identity in signature if identity is not None)
            if len(layers) < 2 or layers[0][0] != "background":
                layers = ()
            try:
                self.wallpaper = load_wallpaper(path, layers, manifest if manifest_identity else None, warn)
            except IMAGE_ERRORS as exc:
                if strict:
                    raise
                warn(f"{path}: {exc}")
        return True
