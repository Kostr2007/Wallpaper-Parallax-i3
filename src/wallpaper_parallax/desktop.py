"""X11 wallpaper surfaces and the horizontal crop animation."""

from dataclasses import dataclass
import os

from PIL import Image
from Xlib import X, Xatom, display
from Xlib.ext import shape


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


@dataclass
class Motion:
    start: float
    target: float
    since: float

    def position(self, now, duration):
        if duration == 0 or now >= self.since + duration:
            return self.target
        t = max(0, (now - self.since) / duration)
        eased = t * t * (3 - 2 * t)
        return self.start + (self.target - self.start) * eased

    def retarget(self, target, now, duration):
        if self.target != target:
            self.start = self.position(now, duration)
            self.target = target
            self.since = now

    def moving(self, now, duration):
        return self.start != self.target and now < self.since + duration


class View:
    def __init__(self, desktop, rect, target, image, travel_percent, now):
        self.desktop = desktop
        self.rect = rect
        self.width, self.height = rect[2:]
        self.travel = round(self.width * travel_percent / 100)
        self.motion = Motion(target, target, now)
        self.pixmap = None
        self.last_x = None

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

        self.load(image)
        self.window.map()
        self.draw(now, 0)

    def load(self, image):
        strip = panorama(image, self.width, self.height, self.travel)
        pixmap = self.desktop.root.create_pixmap(*strip.size, self.desktop.screen.root_depth)
        pixmap.put_pil_image(self.desktop.gc, 0, 0, strip)
        if self.pixmap is not None:
            self.pixmap.free()
        self.pixmap = pixmap
        self.last_x = None

    def draw(self, now, duration):
        x = round(self.travel * self.motion.position(now, duration))
        if x != self.last_x:
            self.window.copy_area(self.desktop.gc, self.pixmap, x, 0, self.width, self.height, 0, 0)
            self.last_x = x

    def close(self):
        self.window.destroy()
        self.pixmap.free()


class Desktop:
    def __init__(self):
        self.connection = display.Display()
        try:
            self.screen = self.connection.screen()
            self.root = self.screen.root
            self.views = {}
            self.check_display()
            self.claim_selection()
            self.gc = self.root.create_gc(graphics_exposures=False)
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

    def configure(self, layout, image, image_changed, args, now):
        if image is None:
            layout = {}
        for name, view in list(self.views.items()):
            if name not in layout or view.rect != layout[name][0]:
                view.close()
                del self.views[name]

        for name, (rect, target) in layout.items():
            if name in self.views:
                view = self.views[name]
                if image_changed:
                    view.load(image)
                view.motion.retarget(target, now, args.duration)
            else:
                self.views[name] = View(self, rect, target, image, args.travel, now)

    def paint(self, now, duration):
        exposed = set()
        while self.connection.pending_events():
            event = self.connection.next_event()
            if event.type == X.Expose:
                exposed.add(event.window.id)

        for view in self.views.values():
            if view.window.id in exposed:
                view.last_x = None
            view.draw(now, duration)
        self.connection.flush()

    def close(self):
        self.connection.close()
