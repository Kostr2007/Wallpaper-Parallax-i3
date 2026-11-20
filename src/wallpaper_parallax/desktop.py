"""X11 wallpaper surfaces and the horizontal crop animation."""

from math import ceil, cos, floor, sin, tau
import os

from PIL import Image
from Xlib import X, Xatom, display
from Xlib.ext import shape

from .motion import Motion, SPRING_MARGIN
from .source import IMAGE_ERRORS
from .xrender import Render

RELATIVE_TRAVEL = {
    "background": 0.40, "atmosphere": 0.55, "middle": 0.75,
    "surface": 0.75, "foreground": 1.0, "particles": 1.0,
}


def crop_box(source_size, target_size):
    """Crop to cover the screen and its motion margins."""
    width, height = source_size
    target_width, target_height = target_size
    scale = max(target_width / width, target_height / height)
    left = max(0, (width - target_width / scale) / 2)
    top = max(0, (height - target_height / scale) / 2)
    return left, top, width - left, height - top


class View:
    def __init__(self, desktop, rect, target, source, travel_percent, now):
        self.desktop = desktop
        self.rect = rect
        self.width, self.height = rect[2:]
        self.travel = round(self.width * travel_percent / 100)
        self.motion = Motion(target, now, source.motion)
        self.wallpaper = None
        self.surfaces = []
        self.buffer = self.buffer_picture = None
        self.use_layers = False
        self.margin = 0
        self.depth = 0
        self.last_offsets = None
        self.ambient_since = now
        self.ambient = None
        self.drift = {}
        self.visible = True

        self.window = desktop.root.create_window(
            *rect, 0, desktop.screen.root_depth, X.InputOutput, X.CopyFromParent,
            override_redirect=True, event_mask=X.ExposureMask | X.VisibilityChangeMask,
        )
        self.window.set_wm_name("Wallpaper Parallax")
        self.window.set_wm_class("wallpaper-parallax", "WallpaperParallax")
        self.window.change_property(
            desktop.atom("_NET_WM_WINDOW_TYPE"), Xatom.ATOM, 32,
            [desktop.atom("_NET_WM_WINDOW_TYPE_DESKTOP")],
        )
        self.window.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
        self.window.configure(stack_mode=X.Below)

        self.configure(source, target, now)
        self.window.map()
        self.draw(now)

    def configure(self, source, target, now):
        layered = bool(source.depth and source.wallpaper.layers and self.desktop.render.supported)
        margin = ceil(self.travel * SPRING_MARGIN) if source.motion.spring else 0
        ambient = source.wallpaper.ambient if layered and source.ambient else None
        if ambient is not self.ambient:
            self.ambient_since = now
        if source.wallpaper is not self.wallpaper or layered != self.use_layers or margin != self.margin:
            self.wallpaper = source.wallpaper
            self.use_layers = layered
            self.margin = margin
            self.load()
        self.ambient = ambient
        self.depth = source.depth
        self.motion.retarget(target, now, source.motion)

    def load(self):
        self.free_surfaces()
        render = self.desktop.render
        root = self.desktop.root
        if self.use_layers:
            self.buffer = root.create_pixmap(self.width, self.height, 24)
            self.buffer_picture = render.picture(self.buffer, 24)
            images = self.wallpaper.layers
        else:
            images = (("flat", self.wallpaper.preview),)

        self.drift = {}
        if self.use_layers and self.wallpaper.ambient is not None:
            self.drift = {
                name: (round(self.width * x / 100), round(self.height * y / 100), phase * tau)
                for name, (x, y, phase) in self.wallpaper.ambient.layers.items()
            }
        pad_x = max((x for x, _, _ in self.drift.values()), default=0)
        pad_y = max((y for _, y, _ in self.drift.values()), default=0)
        self.padding = pad_x, pad_y
        self.strip_size = self.width + self.travel + 2 * (self.margin + pad_x), self.height + 2 * pad_y
        self.crop = crop_box(self.wallpaper.preview.size, self.strip_size)
        for name, image in images:
            strip = image.resize(self.strip_size, Image.Resampling.LANCZOS, self.crop)
            alpha = name not in ("background", "flat")
            pixmap = root.create_pixmap(*strip.size, 32 if alpha else 24)
            picture = None
            if alpha:
                render.upload_alpha(pixmap, strip)
                picture = render.picture(pixmap, 32)
            else:
                pixmap.put_pil_image(self.desktop.gc, 0, 0, strip)
            self.surfaces.append((name, pixmap, picture))
        self.last_offsets = None

    def draw_effect(self, texture, number, x, y):
        clip, _, picture = texture
        row, column = divmod(number, clip.columns)
        left, top, right, bottom = self.crop
        width, height = self.wallpaper.preview.size
        scale_x = (right - left) * clip.width / (self.strip_size[0] * width)
        scale_y = (bottom - top) * clip.height / (self.strip_size[1] * height)
        tx = column * (clip.width + 2) + 1 + left * clip.width / width + x * scale_x
        ty = row * (clip.height + 2) + 1 + top * clip.height / height + y * scale_y
        render = self.desktop.render
        render.transform(picture, scale_x, scale_y, tx, ty)
        render.over(picture, self.buffer_picture, 0, 0, self.width, self.height)

    def draw(self, now):
        position, velocity = self.motion.sample(now)
        minimum = -self.margin / self.travel if self.travel else 0
        position_clamped = min(1 - minimum, max(minimum, position))
        pad_x, pad_y = self.padding
        angle = tau * (now - self.ambient_since) / self.ambient.period if self.ambient else 0
        frame = self.desktop.effect_frame(now) if self.use_layers else None
        offsets = []
        for name, _, _ in self.surfaces:
            x = self.margin + self.travel * (
                0.5 + (position_clamped - 0.5) * (
                    1 - (1 - RELATIVE_TRAVEL.get(name, 1)) * self.depth / 100
                )
            )
            if self.ambient and name in self.drift:
                dx, dy, phase = self.drift[name]
                x += dx * sin(angle + phase)
                y = dy * cos(angle + phase)
            else:
                y = 0
            offsets.append((pad_x + round(x), pad_y + round(y)))
        if (offsets, frame) != self.last_offsets:
            target = self.buffer if self.use_layers else self.window
            _, background, _ = self.surfaces[0]
            x, y = offsets[0]
            target.copy_area(
                self.desktop.gc, background, x, y, self.width, self.height, 0, 0,
            )
            if self.use_layers:
                for (name, _, picture), (x, y) in zip(self.surfaces[1:], offsets[1:]):
                    texture = self.desktop.textures.get(name) if frame is not None else None
                    if texture is not None:
                        self.draw_effect(texture, frame, x, y)
                    else:
                        self.desktop.render.over(picture, self.buffer_picture, x, y, self.width, self.height)
                self.window.copy_area(
                    self.desktop.gc, self.buffer, 0, 0, self.width, self.height, 0, 0,
                )
            self.last_offsets = offsets, frame
        return position != self.motion.target or velocity != 0

    def free_surfaces(self):
        for _, pixmap, picture in self.surfaces:
            if picture is not None:
                self.desktop.render.free(picture)
            pixmap.free()
        self.surfaces.clear()
        if self.buffer is not None:
            self.desktop.render.free(self.buffer_picture)
            self.buffer.free()
            self.buffer = self.buffer_picture = None

    def close(self):
        self.window.destroy()
        self.free_surfaces()


class Desktop:
    def __init__(self, warn):
        self.connection = display.Display()
        try:
            self.screen = self.connection.screen()
            self.root = self.screen.root
            self.views = {}
            self.textures = {}
            self.animation = None
            self.animation_start = 0
            self.warn = warn
            self.warned_render = False
            self.check_display()
            self.claim_selection()
            self.gc = self.root.create_gc(graphics_exposures=False)
            self.render = Render(self.connection)
        except BaseException:
            self.close()
            raise

    def check_display(self):
        info = self.connection.display.info
        fmt = next(fmt for fmt in info.pixmap_formats if fmt.depth == self.screen.root_depth)
        visual = next(
            visual for depth in self.screen.allowed_depths for visual in depth.visuals
            if visual.visual_id == self.screen.root_visual
        )
        if (
            fmt.depth != 24 or fmt.bits_per_pixel != 32 or fmt.scanline_pad != 32
            or info.image_byte_order != X.LSBFirst or info.bitmap_format_scanline_unit != 32
            or (visual.red_mask, visual.green_mask, visual.blue_mask) != (0xff0000, 0xff00, 0xff)
        ):
            raise ValueError("expected an X11 display with a 24-bit TrueColor root visual")
        if not self.connection.has_extension("SHAPE"):
            raise ValueError("the X11 server needs the SHAPE extension")
        version = self.connection.shape_query_version()
        if (version.major_version, version.minor_version) < (1, 1):
            raise ValueError("the X11 server needs SHAPE 1.1 for input-transparent wallpaper")

    def atom(self, name):
        return self.connection.intern_atom(name)

    def claim_selection(self):
        selection = self.atom(f"_WALLPAPER_PARALLAX_I3_S{self.connection.get_default_screen()}")
        owner = self.root.create_window(0, 0, 1, 1, 0, 0, X.InputOnly, X.CopyFromParent)
        self.connection.grab_server()
        try:
            if self.connection.get_selection_owner(selection):
                raise ValueError("wallpaper-parallax is already running on this X11 screen")
            owner.set_selection_owner(selection, X.CurrentTime)
        finally:
            self.connection.ungrab_server()
            self.connection.flush()

    def socket_path(self):
        if path := os.environ.get("I3SOCK"):
            return path
        prop = self.root.get_full_property(self.atom("I3_SOCKET_PATH"), X.AnyPropertyType)
        if prop is None:
            raise ValueError("i3 IPC socket not found on the X11 root window")
        return os.fsdecode(prop.value.rstrip(b"\0"))

    def configure(self, layout, source, travel, now):
        if source.wallpaper is None:
            layout = {}
        elif source.depth and source.wallpaper.layers and not self.render.supported and not self.warned_render:
            self.warn("layered wallpaper needs XRender with 24-bit RGB and 32-bit ARGB formats")
            self.warned_render = True
        animation = None
        if layout and source.depth and self.render.supported and source.ambient:
            ambient = source.wallpaper.ambient
            if ambient and ambient.filmstrips:
                animation = ambient
        if animation is not self.animation:
            self.load_effects(animation, now)
        for name, view in list(self.views.items()):
            if name not in layout or view.rect != layout[name][0]:
                view.close()
                del self.views[name]

        for name, (rect, target) in layout.items():
            if name in self.views:
                self.views[name].configure(source, target, now)
            else:
                self.views[name] = View(self, rect, target, source, travel, now)

    def load_effects(self, animation, now):
        for _, pixmap, picture in self.textures.values():
            self.render.free(picture)
            pixmap.free()
        self.textures.clear()
        self.animation = animation
        self.animation_start = now
        if animation is None:
            return
        for name, clip in animation.filmstrips.items():
            try:
                with Image.open(clip.path) as image:
                    image.load()
                    if image.size != clip.size or image.mode != "RGBA":
                        raise ValueError("filmstrip geometry changed during loading")
                    pixmap = self.root.create_pixmap(*clip.size, 32)
                    self.render.upload_alpha(pixmap, image)
                picture = self.render.picture(pixmap, 32)
                self.render.bilinear(picture)
                self.textures[name] = clip, pixmap, picture
            except IMAGE_ERRORS as exc:
                self.warn(f"{clip.path}: {exc}")

    def effect_frame(self, now):
        if self.textures:
            rate = self.animation.frames / self.animation.period
            return floor((now - self.animation_start) * rate) % self.animation.frames
        return None

    def ambient_deadline(self, now, max_fps):
        if self.textures:
            rate = self.animation.frames / self.animation.period
            if rate <= max_fps:
                next_frame = floor((now - self.animation_start) * rate) + 1
                return max(now + .001, self.animation_start + next_frame / rate)
        return now + 1 / max_fps

    def paint(self, now):
        exposed = set()
        visibility = {}
        while self.connection.pending_events():
            event = self.connection.next_event()
            if event.type == X.Expose:
                exposed.add(event.window.id)
            elif event.type == X.VisibilityNotify:
                visibility[event.window.id] = event.state != X.VisibilityFullyObscured

        moving = False
        for view in self.views.values():
            window = view.window.id
            if window in visibility:
                view.visible = visibility[window]
            if window in exposed:
                view.last_offsets = None
            if view.visible:
                moving |= view.draw(now)
        self.connection.flush()
        return moving

    @property
    def ambient(self):
        return any(view.visible and (self.textures or (view.ambient and view.drift)) for view in self.views.values())

    def close(self):
        self.connection.close()
