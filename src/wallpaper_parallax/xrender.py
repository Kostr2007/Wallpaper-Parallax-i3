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


class CompositeOver(rq.Request):
    _request = rq.Struct(
        rq.Card8("opcode"), rq.Opcode(8), rq.RequestLength(),
        rq.Set("operator", 1, (3,)), rq.Pad(3),  # PictOpOver
        rq.Card32("src"), rq.Card32("mask"), rq.Card32("dst"),
        rq.Int16("src_x"), rq.Pad(10), rq.Card16("width"), rq.Card16("height"),
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

    def over(self, src, dst, x, width, height):
        CompositeOver(
            display=self.display, opcode=self.opcode, operator=3, src=src,
            mask=X.NONE, dst=dst, src_x=x, width=width, height=height,
        )

    def upload_alpha(self, pixmap, image):
        # XRender consumes premultiplied BGRA bytes on the supported X11 visual.
        r, g, b, a = image.convert("RGBa").split()
        pixels = Image.merge("RGBA", (b, g, r, a)).tobytes()
        width, height = image.size
        stride = width * 4
        rows = (self.display.info.max_request_length * 4 - 24) // stride
        gc = pixmap.create_gc(graphics_exposures=False)
        try:
            for y in range(0, height, rows):
                count = min(rows, height - y)
                chunk = pixels[y * stride:(y + count) * stride]
                pixmap.put_image(gc, 0, y, width, count, X.ZPixmap, 32, 0, chunk)
        finally:
            gc.free()
