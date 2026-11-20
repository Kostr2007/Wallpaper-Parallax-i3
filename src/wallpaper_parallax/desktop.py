"""X11 wallpaper surfaces and the horizontal crop animation."""

from math import ceil
import os

from PIL import Image
from Xlib import X, Xatom, display
from Xlib.ext import shape

from .motion import Motion, SPRING_MARGIN
from .xrender import Render

RELATIVE_TRAVEL = {"background": 0.40, "middle": 0.75, "foreground": 1.0}


def panorama(image, width, height, travel):
    """Scale to cover the screen plus the motion range, with a centered crop."""
    width += travel
    scale = max(width / image.width, height / image.height)
    crop_width = min(image.width, width / scale)
    crop_height = min(image.height, height / scale)
    left = (image.width - crop_width) / 2
    top = (image.height - crop_height) / 2
    return image.resize(
        (width, height), Image.Resampling.LANCZOS,
        (left, top, image.width - left, image.height - top),
    )


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

        self.window = desktop.root.create_window(
            *rect, 0, desktop.screen.root_depth, X.InputOutput, X.CopyFromParent,
            override_redirect=True, event_mask=X.ExposureMask,
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
        if source.wallpaper is not self.wallpaper or layered != self.use_layers or margin != self.margin:
            self.wallpaper = source.wallpaper
            self.use_layers = layered
            self.margin = margin
            self.load()
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

        for name, image in images:
            strip = panorama(image, self.width, self.height, self.travel + 2 * self.margin)
            alpha = name in ("middle", "foreground")
            pixmap = root.create_pixmap(*strip.size, 32 if alpha else 24)
            picture = None
            if alpha:
                render.upload_alpha(pixmap, strip)
                picture = render.picture(pixmap, 32)
            else:
                pixmap.put_pil_image(self.desktop.gc, 0, 0, strip)
            self.surfaces.append((name, pixmap, picture))
        self.last_offsets = None

    def draw(self, now):
        position, velocity = self.motion.sample(now)
        minimum = -self.margin / self.travel if self.travel else 0
        position_clamped = min(1 - minimum, max(minimum, position))
        offsets = tuple(
            round(self.margin + self.travel * (
                0.5 + (position_clamped - 0.5) * (
                    1 - (1 - RELATIVE_TRAVEL.get(name, 1)) * self.depth / 100
                )
            ))
            for name, _, _ in self.surfaces
        )
        if offsets != self.last_offsets:
            target = self.buffer if self.use_layers else self.window
            _, background, _ = self.surfaces[0]
            target.copy_area(
                self.desktop.gc, background, offsets[0], 0, self.width, self.height, 0, 0,
            )
            if self.use_layers:
                for (_, _, picture), x in zip(self.surfaces[1:], offsets[1:]):
                    self.desktop.render.over(picture, self.buffer_picture, x, self.width, self.height)
                self.window.copy_area(
                    self.desktop.gc, self.buffer, 0, 0, self.width, self.height, 0, 0,
                )
            self.last_offsets = offsets
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
        for name, view in list(self.views.items()):
            if name not in layout or view.rect != layout[name][0]:
                view.close()
                del self.views[name]

        for name, (rect, target) in layout.items():
            if name in self.views:
                self.views[name].configure(source, target, now)
            else:
                self.views[name] = View(self, rect, target, source, travel, now)

    def paint(self, now):
        exposed = set()
        while self.connection.pending_events():
            event = self.connection.next_event()
            if event.type == X.Expose:
                exposed.add(event.window.id)

        moving = False
        for view in self.views.values():
            if view.window.id in exposed:
                view.last_offsets = None
            moving |= view.draw(now)
        self.connection.flush()
        return moving

    def close(self):
        self.connection.close()
