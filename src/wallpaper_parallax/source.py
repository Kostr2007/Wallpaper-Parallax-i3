"""Load a static wallpaper or follow a desktop wallpaper picker's state file."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageOps


def file_identity(path):
    if path is not None:
        try:
            stat = path.stat()
            return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size
        except FileNotFoundError:
            pass
    return None


def load_rgb(path):
    def decode(filename):
        with Image.open(filename) as original:
            original = ImageOps.exif_transpose(original)
            if "A" in original.getbands() or "transparency" in original.info:
                rgba = original.convert("RGBA")
                background = Image.new("RGB", rgba.size)
                background.paste(rgba, mask=rgba.getchannel("A"))
                return background
            return original.convert("RGB")

    if path.suffix.lower() != ".jxl" or not (decoder := shutil.which("djxl")):
        return decode(path)
    with tempfile.TemporaryDirectory() as directory:
        png = Path(directory) / "wallpaper.png"
        subprocess.run(
            [decoder, str(path.resolve()), str(png)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
        )
        return decode(png)


class Source:
    def __init__(self, image, state_file):
        self.path = image
        self.state_file = state_file
        self.state_identity = None
        self.image_identity = None
        self.image = None
        self.fit = "fill"
        if image is not None:
            self.image = load_rgb(image)
            self.image_identity = (image, file_identity(image))

    def refresh(self, warn):
        state_identity = file_identity(self.state_file)
        if state_identity != self.state_identity:
            self.state_identity = state_identity
            self.path = None
            if state_identity is not None:
                try:
                    state = json.loads(self.state_file.read_text(encoding="utf-8"))
                    image = Path(state["image"])
                    if not image.is_absolute():
                        raise ValueError("image path must be absolute")
                    fit = state.get("fit", "fill")
                    if fit not in ("fill", "center", "max", "tile"):
                        raise ValueError(f"unknown fit mode {fit!r}")
                    self.fit = fit
                    self.path = image
                except (OSError, TypeError, KeyError, ValueError) as exc:
                    warn(f"{self.state_file}: {exc}")

        path = self.path if self.fit == "fill" else None
        image_identity = (path, file_identity(path))
        if image_identity == self.image_identity:
            return False

        self.image_identity = image_identity
        self.image = None
        if path is not None:
            try:
                self.image = load_rgb(path)
            except (OSError, ValueError, subprocess.SubprocessError, Image.DecompressionBombError) as exc:
                warn(f"{path}: {exc}")
        return True
