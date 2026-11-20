"""Exercise the installed command against two i3 outputs on a private Xvfb."""

from contextlib import ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from PIL import Image, ImageDraw
from Xlib import X, display


def wait_for(check, description, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.015)
    raise AssertionError(f"timed out waiting for {description}")


def start(stack, log, env, args, **kwargs):
    process = subprocess.Popen(args, env=env, stdout=log, stderr=log, **kwargs)

    def stop():
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    stack.callback(stop)
    return process


def smoke(stack, directory, log):
    env = {k: v for k, v in os.environ.items() if k not in (
        "DISPLAY", "XAUTHORITY", "I3SOCK", "WAYLAND_DISPLAY", "SWAYSOCK",
    )}
    runtime = directory / "runtime"
    runtime.mkdir(mode=0o700)
    env.update(HOME=str(directory), XDG_RUNTIME_DIR=str(runtime), LC_ALL="C")

    with (directory / "display").open("w+") as display_file:
        fd = display_file.fileno()
        start(stack, log, env, [
            "Xvfb", "-displayfd", str(fd), "-screen", "0", "1024x360x24", "-nolisten", "tcp",
        ], pass_fds=(fd,))

        def display_number():
            display_file.seek(0)
            return display_file.readline().strip()

        env["DISPLAY"] = ":" + wait_for(display_number, "Xvfb")
    x11 = display.Display(env["DISPLAY"])
    stack.callback(x11.close)
    root = x11.screen().root

    config = directory / "i3.config"
    config.write_text(
        "font pango:monospace 9\n"
        "fake-outputs 512x360+0+0,512x360+512+0\n"
        "workspace 1 output fake-0\n"
        "workspace 2 output fake-1\n"
        "workspace 5 output fake-0\n"
    )
    start(stack, log, env, ["i3", "-c", str(config)])
    socket_atom = x11.intern_atom("I3_SOCKET_PATH")
    wait_for(lambda: root.get_full_property(socket_atom, X.AnyPropertyType), "i3 IPC")

    def i3(*args):
        reply = subprocess.check_output(["i3-msg", "-r", *args], env=env)
        return json.loads(reply)

    i3("workspace number 1")
    existing_window = root.create_window(
        400, 300, 32, 32, 0, x11.screen().root_depth, X.InputOutput, X.CopyFromParent,
        override_redirect=True, background_pixel=0xc0ffee,
    )
    existing_window.map()
    x11.sync()
    source = directory / "stripes.png"
    data = bytes(channel for x in range(768) for channel in (x % 256, x // 256 * 80, 20))
    Image.frombytes("RGB", (768, 360), data * 360).save(source)
    state = directory / "wallpaper.json"

    def set_state(image, fit="fill", **options):
        tmp = state.with_suffix(".tmp")
        tmp.write_text(json.dumps({"image": str(image), "fit": fit, **options}))
        tmp.replace(state)

    def views():
        surfaces = [
            w for w in root.query_tree().children
            if w.get_wm_class() == ("wallpaper-parallax", "WallpaperParallax")
            and w.get_attributes().map_state == X.IsViewable
        ]
        return sorted(surfaces, key=lambda window: window.get_geometry().x)

    def pixel(window, x=0, y=0):
        return tuple(window.get_image(x, y, 1, 1, X.ZPixmap, 0xffffff).data[:3])

    def pixels(window):
        return window.get_image(0, 0, 16, 16, X.ZPixmap, 0xffffff).data

    def digest(window):
        return sha256(pixels(window)).hexdigest()

    set_state(source)
    command = [
        "wallpaper-parallax", "--state-file", str(state), "--pages", "5",
        "--travel", "50", "--duration", "400",
    ]
    daemon = start(stack, log, env, command)
    left, right = wait_for(lambda: v if len(v := views()) == 2 else None, "two wallpaper windows")
    stack_order = root.query_tree().children
    assert stack_order.index(left) < stack_order.index(existing_window)
    assert stack_order.index(right) < stack_order.index(existing_window)
    for surface in (left, right):
        assert surface.get_attributes().override_redirect
        assert not surface.shape_get_rectangles(2).rectangles
    first, right_before = digest(left), digest(right)
    assert pixels(left)[:3] == bytes((20, 0, 0))

    duplicate = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5)
    assert duplicate.returncode == 1 and "already running" in duplicate.stderr

    i3("workspace number 5")
    intermediate = wait_for(lambda: h if (h := digest(left)) != first else None, "motion to start")
    wait_for(lambda: pixels(left)[:3] == bytes((20, 80, 0)), "rightmost crop")
    last = digest(left)
    assert len({first, intermediate, last}) == 3
    assert digest(right) == right_before

    i3("workspace number 1")
    time.sleep(0.14)
    i3("workspace number 5")
    wait_for(lambda: digest(left) == last, "rapid reversal")
    i3("workspace number 2")
    time.sleep(0.45)
    assert digest(left) == last and digest(right) == right_before

    replacement = directory / "new wallpaper.png"
    Image.new("RGB", (768, 360), (10, 20, 30)).save(replacement)
    set_state(replacement)
    wait_for(lambda: pixels(left)[:3] == bytes((30, 20, 10)), "picker image update")
    assert views()[0].id == left.id

    jxl = directory / "wallpaper.jxl"
    subprocess.run(["cjxl", str(source), str(jxl), "-d", "0"], env=env, check=True, stdout=log, stderr=log)
    set_state(jxl)
    wait_for(lambda: digest(left) == last, "JPEG XL decode")
    assert views()[0].id == left.id

    set_state(replacement, "center")
    wait_for(lambda: not views(), "static fit fallback")
    set_state(replacement)
    wait_for(lambda: len(views()) == 2, "return to animated fill")
    # The three planes share the same canvas, with distinct travel and alpha.
    left, right = views()
    scene = directory / "layered.png"
    Image.new("RGB", (768, 360), (1, 2, 3)).save(scene)
    companions = scene.with_suffix(".parallax")
    companions.mkdir()
    Image.open(source).save(companions / "background.png")
    middle = Image.new("RGBA", (768, 360))
    ImageDraw.Draw(middle).rectangle((150, 10, 199, 19), fill=(0, 255, 0, 255))
    middle.save(companions / "middle.png")
    foreground = Image.new("RGBA", (768, 360))
    ImageDraw.Draw(foreground).rectangle((400, 10, 419, 19), fill=(40, 120, 200, 128))
    foreground.save(companions / "foreground.png")

    i3("workspace number 1")
    set_state(scene, motion="instant", depth=100)
    wait_for(lambda: pixel(left) == (20, 0, 77), "far plane at the first workspace")
    assert pixel(left, 125, 15) == (0, 255, 0)
    assert pixel(left, 410, 15) == (110, 100, 135)
    unaffected = digest(right)
    i3("workspace number 5")
    wait_for(lambda: pixel(left) == (20, 0, 179), "far plane at the last workspace")
    assert pixel(left, 125, 15) == (20, 80, 48)
    assert pixel(left, 154, 15) == (110, 100, 58)
    assert digest(right) == unaffected
    set_state(scene, motion="instant", depth=0)
    wait_for(lambda: pixel(left) == (3, 2, 1), "depth disabled at runtime")
    assert views()[0].id == left.id

    # Both motion axes work while i3 is idle, even when workspace motion is instant.
    atmosphere = Image.new("RGBA", (768, 360))
    paint = ImageDraw.Draw(atmosphere)
    for y in range(0, 360, 8):
        for x in range(0, 768, 8):
            paint.rectangle((x, y, x + 7, y + 7), fill=(x // 4, y // 2, 255, 255))
    atmosphere.save(companions / "atmosphere.png")
    manifest = companions / "motion.json"
    manifest.write_text(json.dumps({"version": 1, "period": 2, "layers": {"atmosphere": [5, 5, 0]}}))
    set_state(scene, motion="instant", depth=100, ambient=False)
    wait_for(lambda: pixel(left)[0] == 255, "atmosphere compositing")
    still = digest(left)
    set_state(scene, motion="instant", depth=100, ambient=True)
    wait_for(lambda: digest(left) != still, "ambient motion while workspace is idle")
    initial = pixel(left)
    wait_for(lambda: pixel(left)[1] != initial[1], "vertical ambient motion")
    wait_for(lambda: pixel(left)[2] != initial[2], "horizontal ambient motion")
    cover = root.create_window(0, 0, 512, 360, 0, x11.screen().root_depth, X.InputOutput,
                               X.CopyFromParent, override_redirect=True)
    cover.map()
    x11.sync()
    time.sleep(0.14)
    assert daemon.poll() is None
    cover.destroy()
    x11.sync()
    wait_for(lambda: pixel(left)[0] == 255, "restore live layers after exposure")
    set_state(scene, motion="instant", depth=100, ambient=False)
    wait_for(lambda: digest(left) == still, "disable ambient motion without changing crop")
    time.sleep(0.3)
    assert digest(left) == still

    manifest.write_text('{"version":1,"period":2,"layers":{"unknown":[5,5,0]}}')
    set_state(scene, motion="instant", depth=100, ambient=True)
    wait_for(lambda: digest(left) != still, "reload invalid manifest with static cropping")
    invalid = digest(left)
    time.sleep(0.3)
    assert digest(left) == invalid

    # A v2 track replaces pixels at a fixed camera position. Its frame grid
    # has both rows and columns, and the same texture serves both outputs.
    Image.new("RGBA", (768, 360), (32, 48, 96, 255)).save(companions / "atmosphere.png")
    atlas = Image.new("RGBA", (12, 12))
    atlas_colors = ((231, 40, 60), (20, 202, 95), (40, 70, 214), (218, 178, 24))
    for index, color in enumerate(atlas_colors):
        x, y = (index % 2) * 6, (index // 2) * 6
        atlas.paste((*color, 255), (x, y, x + 6, y + 6))
    atlas_path = companions / "atmosphere.loop.png"
    atlas.save(atlas_path)
    manifest.write_text(json.dumps({
        "version": 2, "period": 2, "frames": 4,
        "tracks": {"atmosphere": {"width": 4, "height": 4, "columns": 2}},
    }))
    for color in atlas_colors:
        expected = color[::-1]
        wait_for(lambda: pixel(left, 125, 50) == expected, "filmstrip frame")
        assert pixel(left, 510, 355) in tuple(color[::-1] for color in atlas_colors)
    assert pixel(right, 125, 50) in tuple(color[::-1] for color in atlas_colors)
    set_state(scene, motion="instant", depth=100, ambient=False)
    wait_for(lambda: pixel(left, 125, 50) == (96, 48, 32), "filmstrip freeze fallback")
    set_state(scene, motion="instant", depth=100, ambient=True)
    wait_for(lambda: pixel(left, 125, 50) != (96, 48, 32), "filmstrip resumed")
    atlas_path.unlink()
    wait_for(lambda: pixel(left, 125, 50) == (96, 48, 32), "missing filmstrip uses poster")

    # A linked illumination and its receiver must travel together at both
    # edges of the panorama, including when the effects are frozen.
    upper = Image.new("RGBA", (768, 360))
    ImageDraw.Draw(upper).rectangle((308, 60, 347, 86), fill=(214, 96, 35, 255))
    upper.save(companions / "atmosphere.png")
    lower = Image.new("RGBA", upper.size)
    ImageDraw.Draw(lower).rectangle((308, 100, 347, 126), fill=(22, 180, 77, 255))
    lower.save(companions / "foreground.png")
    tile = upper.resize((384, 180), Image.Resampling.BOX)
    atlas = Image.new("RGBA", (772, 182))
    atlas.paste(tile, (1, 1))
    atlas.paste(tile, (387, 1))
    atlas.save(atlas_path)
    manifest.write_text(json.dumps({
        "version": 2, "period": 2, "frames": 2,
        "tracks": {"atmosphere": {"width": 384, "height": 180, "columns": 2, "parallax": "foreground"}},
    }))

    def aligned(x):
        return pixel(left, x, 71) == (35, 96, 214) and pixel(left, x, 112) == (77, 180, 22)

    i3("workspace number 1")
    set_state(scene, motion="instant", depth=100, ambient=True)
    wait_for(lambda: aligned(327), "filmstrip aligned at first workspace")
    i3("workspace number 5")
    wait_for(lambda: aligned(71), "filmstrip aligned at last workspace")
    set_state(scene, motion="instant", depth=100, ambient=False)
    wait_for(lambda: aligned(71), "poster aligned at last workspace")
    i3("workspace number 1")
    wait_for(lambda: aligned(327), "poster aligned at first workspace")

    # A view can overshoot even at the last workspace because the crop has room.
    spring_image = directory / "spring.png"
    row = bytes(channel for x in range(896) for channel in (x * 255 // 895, 0, 0))
    Image.frombytes("RGB", (896, 360), row * 360).save(spring_image)
    i3("workspace number 1")
    set_state(spring_image, motion="spring", inertia=100)
    wait_for(lambda: pixel(left) == (0, 0, 18), "spring starting crop")
    i3("workspace number 5")
    wait_for(lambda: pixel(left)[2] > 96, "spring overshoot beyond endpoint")
    wait_for(lambda: pixel(left) == (0, 0, 91), "spring final crop")

    daemon.terminate()
    assert daemon.wait(timeout=5) == 0
    wait_for(lambda: not views(), "wallpaper surfaces to be removed")
    print("X11 smoke test passed")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="parallax-x11-") as tmp:
        directory = Path(tmp)
        log_path = directory / "session.log"
        try:
            with log_path.open("w") as log, ExitStack() as stack:
                smoke(stack, directory, log)
        except BaseException:
            print(log_path.read_text())
            raise
