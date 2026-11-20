"""Follow i3 workspace focus and animate the wallpaper on each output."""

import argparse
import asyncio
from importlib.metadata import version
import os
from pathlib import Path
import signal
import sys

from Xlib import error as xerror

from . import ipc
from .desktop import Desktop
from .motion import MODES, MotionSettings
from .source import IMAGE_ERRORS, Source


def bounded_int(low, high):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}")
        return number

    return parse


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=version("wallpaper-parallax-i3"))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="wallpaper image")
    source.add_argument("--state-file", type=Path, help="watch a JSON file for the wallpaper path and fit")
    order = parser.add_mutually_exclusive_group()
    order.add_argument("--pages", type=bounded_int(2, 1000), default=5, help="numbered workspaces (default: 5)")
    order.add_argument("--workspace", action="append", help="name in panorama order (repeat for each workspace)")
    parser.add_argument("--travel", type=bounded_int(0, 100), default=20, help="total travel as a %% of screen width (default: 20)")
    parser.add_argument("--duration", type=bounded_int(0, 5000), default=320, help="transition time in milliseconds (default: 320)")
    parser.add_argument("--fps", type=bounded_int(1, 144), default=60, help="frame rate limit (default: 60)")
    parser.add_argument("--motion", choices=MODES, default="smooth", help="workspace transition (default: smooth)")
    parser.add_argument("--inertia", type=bounded_int(0, 100), default=55, help="spring bounce and damping (default: 55)")
    parser.add_argument("--depth", type=bounded_int(0, 100), default=70, help="relative layer motion (default: 70)")
    parser.add_argument("--no-ambient", action="store_true", help="disable continuous motion in live wallpaper packs")
    parser.add_argument("--socket", help="i3 IPC socket path")
    args = parser.parse_args()
    if args.workspace and len(set(args.workspace)) != len(args.workspace):
        parser.error("--workspace names must be unique")
    args.duration /= 1000
    return args


def warn(message):
    print(f"wallpaper-parallax: {message}", file=sys.stderr, flush=True)


def workspace_position(workspace, args):
    if workspace is None:
        return 0.5
    if args.workspace:
        name = workspace["name"]
        if name in args.workspace and len(args.workspace) > 1:
            return args.workspace.index(name) / (len(args.workspace) - 1)
        return 0.5
    number = workspace["num"]
    return min(number - 1, args.pages - 1) / (args.pages - 1) if number > 0 else 0.5


async def get_layout(reader, writer, args):
    outputs = await ipc.request(reader, writer, ipc.GET_OUTPUTS)
    workspaces = await ipc.request(reader, writer, ipc.GET_WORKSPACES)
    visible = {workspace["output"]: workspace for workspace in workspaces if workspace["visible"]}
    layout = {}
    for output in outputs:
        if output["active"]:
            rect = output["rect"]
            bounds = (rect["x"], rect["y"], rect["width"], rect["height"])
            if rect["width"] > 0 and rect["height"] > 0:
                layout[output["name"]] = bounds, workspace_position(visible.get(output["name"]), args)
    return layout


async def run_session(desktop, source, args, commands, events):
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    layout_dirty = True

    async def watch_i3():
        nonlocal layout_dirty
        while True:
            kind, event = await ipc.receive(events)
            if kind == ipc.OUTPUT_EVENT or (
                kind == ipc.WORKSPACE_EVENT and event["change"] != "urgent"
            ):
                layout_dirty = True
                wake.set()

    async def render():
        nonlocal layout_dirty
        source_check = 0
        layout = {}
        while True:
            wake.clear()
            layout_changed = layout_dirty
            if layout_changed:
                layout_dirty = False
                layout = await get_layout(*commands, args)
            source_changed = False
            now = loop.time()
            if now >= source_check:
                previous = source.motion, source.depth, source.ambient
                source_changed = source.refresh(warn) or previous != (source.motion, source.depth, source.ambient)
                source_check = now + 0.5
            if layout_changed or source_changed:
                desktop.configure(layout, source, args.travel, loop.time())

            frame_time = loop.time()
            moving = desktop.paint(frame_time)
            deadline = source_check
            if moving:
                deadline = min(deadline, frame_time + 1 / args.fps)
            elif desktop.ambient:
                deadline = min(deadline, desktop.ambient_deadline(frame_time, min(args.fps, 24)))
            try:
                await asyncio.wait_for(wake.wait(), max(0, deadline - loop.time()))
            except TimeoutError:
                pass

    fd = desktop.connection.fileno()
    loop.add_reader(fd, wake.set)
    tasks = [asyncio.create_task(watch_i3()), asyncio.create_task(render())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        loop.remove_reader(fd)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run(args):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    motion = MotionSettings(args.motion, args.duration, args.inertia)
    source = Source(args.image, args.state_file, motion, args.depth, warn, ambient=not args.no_ambient)
    desktop = Desktop(warn)
    try:
        path = args.socket or desktop.socket_path()
        async with ipc.connect(path) as commands, ipc.connect(path) as events:
            response = await ipc.request(*events, ipc.SUBSCRIBE, b'["workspace","output"]')
            if not response.get("success"):
                raise ValueError("i3 refused the workspace/output subscription")
            await run_session(desktop, source, args, commands, events[0])
    finally:
        desktop.close()
        loop.remove_signal_handler(signal.SIGTERM)


def main():
    args = parse_args()
    if not os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        warn("an i3 session on X11 is required")
        return 1
    try:
        asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0
    except asyncio.IncompleteReadError:
        warn("i3 IPC connection closed")
    except (*IMAGE_ERRORS, xerror.DisplayError, xerror.ConnectionClosedError) as exc:
        warn(str(exc))
    else:
        return 0
    return 1
