"""The XRender requests used to composite premultiplied wallpaper layers."""

from PIL import Image
from Xlib import X
from Xlib.protocol import rq


FORMAT = rq.Struct(
    rq.Card32("id"), rq.Card8("type"), rq.Card8("depth"), rq.Pad(2),
    rq.Card16("red_shift"), rq.Card16("red_mask"),
    rq.Card16("green_shift"), rq.Card16("green_mask"),
    rq.Card16("blue_shift"), rq.Card16("blue_mask"),
    rq.Card16("alpha_shift"), rq.Card16("alpha_mask"), rq.Card32("colormap"),
)


class QueryFormats(rq.ReplyRequest):
    _request = rq.Struct(rq.Card8("opcode"), rq.Opcode(1), rq.RequestLength())
    _reply = rq.Struct(
        rq.ReplyCode(), rq.Pad(1), rq.Card16("sequence_number"), rq.ReplyLength(),
        rq.LengthOf("formats", 4), rq.Pad(20), rq.List("formats", FORMAT, pad=0),
    )


class CreatePicture(rq.Request):
    _request = rq.Struct(
        rq.Card8("opcode"), rq.Opcode(4), rq.RequestLength(),
        rq.Card32("picture"), rq.Drawable("drawable"), rq.Card32("format"), rq.Pad(4),
    )


class FreePicture(rq.Request):
    _request = rq.Struct(
        rq.Card8("opcode"), rq.Opcode(7), rq.RequestLength(), rq.Card32("picture"),
    )


SRC, OVER, ATOP = 1, 3, 9


class Composite(rq.Request):
    _request = rq.Struct(
        rq.Card8("opcode"), rq.Opcode(8), rq.RequestLength(),
        rq.Set("operator", 1, (SRC, OVER, ATOP)), rq.Pad(3),
        rq.Card32("src"), rq.Card32("mask"), rq.Card32("dst"),
        rq.Int16("src_x"), rq.Int16("src_y"), rq.Pad(6), rq.Int16("dst_y"),
        rq.Card16("width"), rq.Card16("height"),
    )


class SetPictureTransform(rq.Request):
    _request = rq.Struct(
        rq.Card8("opcode"), rq.Opcode(28), rq.RequestLength(), rq.Card32("picture"),
        *(rq.Int32(key) for key in ("xx", "xy", "dx", "yx", "yy", "dy", "zx", "zy", "zz")),
    )


class SetBilinearFilter(rq.Request):
    _request = rq.Struct(
        rq.Card8("opcode"), rq.Opcode(30), rq.RequestLength(), rq.Card32("picture"),
        rq.LengthOf("name", 2), rq.Pad(2), rq.String8("name"),
    )


class Render:
    def __init__(self, connection):
        self.display = connection.display
        self.opcode = None
        self.formats = {}
        extension = connection.query_extension("RENDER")
        if extension is None:
            return
        self.opcode = extension.major_opcode
        for fmt in QueryFormats(display=self.display, opcode=self.opcode).formats:
            if (
                fmt.type == 1
                and (fmt.red_shift, fmt.green_shift, fmt.blue_shift) == (16, 8, 0)
                and (fmt.red_mask, fmt.green_mask, fmt.blue_mask) == (255, 255, 255)
                and ((fmt.depth, fmt.alpha_mask) == (24, 0)
                     or (fmt.depth, fmt.alpha_shift, fmt.alpha_mask) == (32, 24, 255))
            ):
                self.formats[fmt.depth] = fmt.id

    @property
    def supported(self):
        return 24 in self.formats and 32 in self.formats

    def picture(self, pixmap, depth):
        picture = self.display.allocate_resource_id()
        CreatePicture(
            display=self.display, opcode=self.opcode, picture=picture,
            drawable=pixmap, format=self.formats[depth],
        )
        return picture

    def free(self, picture):
        FreePicture(display=self.display, opcode=self.opcode, picture=picture)
        self.display.free_resource_id(picture)

    def over(self, src, dst, x, y, width, height):
        self.composite(src, dst, x, y, width, height)

    def composite(self, src, dst, x, y, width, height, *, operator=OVER, dst_y=0):
        Composite(
            display=self.display, opcode=self.opcode, operator=operator, src=src,
            mask=X.NONE, dst=dst, src_x=x, src_y=y, dst_y=dst_y, width=width, height=height,
        )

    def bilinear(self, picture):
        SetBilinearFilter(display=self.display, opcode=self.opcode, picture=picture, name="bilinear")

    def transform(self, picture, sx, sy, x, y, shear=0):
        SetPictureTransform(
            display=self.display, opcode=self.opcode, picture=picture,
            xx=round(sx * 65536), xy=round(shear * 65536), dx=round(x * 65536),
            yx=0, yy=round(sy * 65536), dy=round(y * 65536), zx=0, zy=0, zz=65536,
        )

    def upload_alpha(self, pixmap, image):
        # XRender consumes premultiplied BGRA bytes on the supported X11 visual.
        width, height = image.size
        stride = width * 4
        rows = (self.display.info.max_request_length * 4 - 24) // stride
        gc = pixmap.create_gc(graphics_exposures=False)
        try:
            for index, y in enumerate(range(0, height, rows), 1):
                count = min(rows, height - y)
                rgba = image.crop((0, y, width, y + count)).convert("RGBa")
                r, g, b, a = rgba.split()
                chunk = Image.merge("RGBA", (b, g, r, a)).tobytes()
                pixmap.put_image(gc, 0, y, width, count, X.ZPixmap, 32, 0, chunk)
                # Python Xlib concatenates queued request buffers when it flushes.
                if index % 8 == 0:
                    self.display.flush()
        finally:
            gc.free()
        self.display.flush()
