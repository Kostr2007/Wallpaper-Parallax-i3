#!/usr/bin/env bash
set -euo pipefail

if (( $# < 2 || $# > 3 )) || [[ -n ${3:-} && $3 != --live ]]; then
    echo "Usage: $0 IMAGE OUTPUT_DIRECTORY [--live]" >&2
    exit 2
fi
lead=0.85
duration=5.8
caption='Spring motion  ·  Layered wallpaper  ·  i3'
if [[ ${3:-} == --live ]]; then
    lead=2.2
    duration=7.1
    caption='Live wallpaper  ·  i3'
fi
wallpaper=$(realpath "$1")
output=$(realpath -m "$2")
ffmpeg_bin=${FFMPEG:-ffmpeg}
app=${WALLPAPER_PARALLAX:-wallpaper-parallax}
work=$(mktemp -d)
pids=()

cleanup() {
    if (( ${#pids[@]} )); then
        kill "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT

wait_until() {
    for ((attempt=0; attempt<100; attempt++)); do
        if "$@"; then
            return
        fi
        sleep 0.05
    done
    echo "Timed out waiting for $*" >&2
    cat "$work"/*.log >&2
    return 1
}

unset DISPLAY XAUTHORITY WAYLAND_DISPLAY I3SOCK
export XDG_RUNTIME_DIR="$work/runtime"
mkdir -m 700 "$XDG_RUNTIME_DIR"
mkdir -p "$output"
Xvfb -displayfd 3 -screen 0 1280x720x24 -nolisten tcp 3>"$work/display" >"$work/xvfb.log" 2>&1 &
pids+=("$!")
wait_until test -s "$work/display"
DISPLAY=:"$(cat "$work/display")"
export DISPLAY

cat >"$work/i3.config" <<CONFIG
font pango:DejaVu Sans 10
mouse_warping none
bar {
    position bottom
    font pango:DejaVu Sans Bold 10
    height 34
    workspace_min_width 82
    status_command echo '$caption'; exec sleep infinity
    colors {
        background #101b29
        statusline #c6d9e1
        focused_workspace #6ac4bd #246366 #f0fffc
    }
}
CONFIG

i3 -c "$work/i3.config" >"$work/i3.log" 2>&1 &
pids+=("$!")
i3_ready() { i3-msg -t get_workspaces >/dev/null 2>&1; }
wait_until i3_ready

picom --config /dev/null --backend xrender >"$work/picom.log" 2>&1 &
pids+=("$!")
"$app" --image "$wallpaper" --pages 5 --travel 36 --duration 450 --motion spring --inertia 100 --depth 90 >"$work/app.log" 2>&1 &
pids+=("$!")
wallpaper_ready() { xwininfo -name 'Wallpaper Parallax' >/dev/null 2>&1; }
wait_until wallpaper_ready
sleep 0.35

"$ffmpeg_bin" -hide_banner -loglevel error -y -f x11grab -draw_mouse 0 -framerate 30 \
    -video_size 1280x720 -i "$DISPLAY" -t "$duration" -c:v ffv1 "$work/desktop.mkv" &
recording=$!
pids+=("$recording")
sleep "$lead"
for workspace in 2 4 3 5 1; do
    i3-msg -q "workspace number $workspace"
    sleep 0.85
done
wait "$recording"

"$ffmpeg_bin" -hide_banner -loglevel error -y -i "$work/desktop.mkv" \
    -filter_complex_threads 1 \
    -filter_complex 'fps=16,scale=640:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=112:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5' \
    -loop 0 "$output/demo.gif"
"$ffmpeg_bin" -hide_banner -loglevel error -y -ss 3.20 -i "$work/desktop.mkv" \
    -frames:v 1 -update 1 "$output/demo.png"
