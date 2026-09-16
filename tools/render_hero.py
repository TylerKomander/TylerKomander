"""Render the profile hero: the name typing itself out once, as animated WebP.

Transparent background so it sits on the page as text rather than a plate, crimson so it
holds up in both GitHub themes, and it plays exactly once. Deterministic for a fixed
(Pillow, libwebp, FreeType) triple. Pinned to pillow==12.3.0.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

COPY = {
    "name": "Tyler Komander",
    "alt": "Tyler Komander",
}

ACCENT = (0xC4, 0x1E, 0x3A, 0xFF)
TRANSPARENT = (0, 0, 0, 0)

CANVAS = (900, 150)
DISPLAY = (450, 75)
BASELINE = 104
NAME_FACE = ("consolab.ttf", 88)
CURSOR_W = 40
CURSOR_H = 70
CURSOR_RISE = 62
LOOP_COUNT = 1

MAX_NAME_CHARS = 16
MIN_FRAME_MS = 30
DURATION_BAND_MS = (2000, 8000)
SIZE_WARN = 120 * 1024
SIZE_FAIL = 400 * 1024

FONT_DIR = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ -.")


@lru_cache(maxsize=None)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / name
    if not path.exists():
        sys.exit("missing font: " + str(path))
    return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)


CELL_W = round(font(*NAME_FACE).getlength("M"))
PAD_X = (CANVAS[0] - CELL_W * len(COPY["name"])) // 2


@dataclass(frozen=True)
class State:
    typed: str
    cursor: bool


@dataclass(frozen=True)
class Blink:
    cycles: tuple

    def expand(self, state):
        for index, ms in enumerate(self.cycles):
            state = replace(state, cursor=index % 2 == 0)
            yield state, ms


@dataclass(frozen=True)
class TypeName:
    ms_per_char: int

    def expand(self, state):
        for end in range(1, len(COPY["name"]) + 1):
            state = replace(state, typed=COPY["name"][:end], cursor=True)
            yield state, self.ms_per_char


@dataclass(frozen=True)
class Hold:
    ms: int
    cursor: bool

    def expand(self, state):
        state = replace(state, cursor=self.cursor)
        yield state, self.ms


BEATS = (
    Blink(cycles=(420, 360)),
    TypeName(ms_per_char=70),
    Hold(ms=1200, cursor=True),
    Hold(ms=1600, cursor=False),
)


@lru_cache(maxsize=None)
def render(state: State) -> Image.Image:
    image = Image.new("RGBA", CANVAS, TRANSPARENT)
    draw = ImageDraw.Draw(image)
    glyph_font = font(*NAME_FACE)
    for column, character in enumerate(state.typed):
        draw.text(
            (PAD_X + column * CELL_W, BASELINE),
            character,
            font=glyph_font,
            fill=ACCENT,
            anchor="ls",
        )
    if state.cursor:
        x = PAD_X + len(state.typed) * CELL_W
        draw.rectangle(
            [x, BASELINE - CURSOR_RISE, x + CURSOR_W, BASELINE + CURSOR_H - CURSOR_RISE],
            fill=ACCENT,
        )
    return image


def timeline():
    state = State(typed="", cursor=True)
    frames = []
    for beat in BEATS:
        for state, ms in beat.expand(state):
            frames.append((state, ms))
    merged = []
    for state, ms in frames:
        if merged and merged[-1][0] == state:
            merged[-1] = (state, merged[-1][1] + ms)
        else:
            merged.append((state, ms))
    # A second pass on the rendered pixels, not just the state: libwebp folds
    # pixel-identical frames into their predecessor, which would leave the written ANMF
    # count short of the duration list.
    collapsed = []
    for state, ms in merged:
        digest = hashlib.sha1(render(state).tobytes()).digest()
        if collapsed and collapsed[-1][2] == digest:
            collapsed[-1] = (collapsed[-1][0], collapsed[-1][1] + ms, digest)
        else:
            collapsed.append((state, ms, digest))
    return [(state, ms) for state, ms, _ in collapsed]


def preflight(states) -> None:
    name = COPY["name"]
    if len(name) > MAX_NAME_CHARS:
        sys.exit("name too long (%d > %d)" % (len(name), MAX_NAME_CHARS))
    stray = set(name) - ALLOWED
    if stray:
        sys.exit("name has glyphs outside the allowed set: %s" % sorted(stray))
    right_edge = PAD_X + CELL_W * len(name) + CURSOR_W
    if PAD_X < 0 or right_edge > CANVAS[0]:
        sys.exit("name and cursor do not fit the canvas: right edge %d" % right_edge)
    durations = [ms for _, ms in states]
    if min(durations) < MIN_FRAME_MS:
        sys.exit("frame duration below the %dms floor: %d" % (MIN_FRAME_MS, min(durations)))
    total = sum(durations)
    if not DURATION_BAND_MS[0] <= total <= DURATION_BAND_MS[1]:
        sys.exit("total duration %dms outside %s" % (total, DURATION_BAND_MS))
    final = states[-1][0]
    if final.typed != name or final.cursor:
        sys.exit("the last frame must rest on the full name with no cursor")


def walk_riff(path: Path):
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        sys.exit("not a RIFF/WEBP file")
    if struct.unpack("<I", data[4:8])[0] != len(data) - 8:
        sys.exit("RIFF size field does not match the file length")
    chunks = {}
    offset = 12
    while offset + 8 <= len(data):
        fourcc = data[offset:offset + 4].decode("ascii", "replace")
        size = struct.unpack("<I", data[offset + 4:offset + 8])[0]
        chunks.setdefault(fourcc, []).append(data[offset + 8:offset + 8 + size])
        offset += 8 + size + (size & 1)
    if "VP8X" not in chunks:
        sys.exit("no VP8X chunk")
    flags = chunks["VP8X"][0][0]
    if not flags & 0x02:
        sys.exit("VP8X animation flag not set - decoders would treat this as a still image")
    if not flags & 0x10:
        sys.exit("VP8X alpha flag not set - the background would render as a black plate")
    if "ANIM" not in chunks:
        sys.exit("no ANIM chunk")
    loop_count = struct.unpack("<H", chunks["ANIM"][0][4:6])[0]
    if loop_count != LOOP_COUNT:
        sys.exit("ANIM loop count is %d, expected %d" % (loop_count, LOOP_COUNT))
    for unwanted in ("EXIF", "XMP ", "ICCP"):
        if unwanted in chunks:
            sys.exit("unexpected metadata chunk in a public asset: " + unwanted)
    return {"frames": len(chunks.get("ANMF", [])), "loop": loop_count}


def verify(path: Path, durations):
    riff = walk_riff(path)
    if riff["frames"] != len(durations):
        sys.exit("ANMF count %d != expected %d" % (riff["frames"], len(durations)))
    with Image.open(path) as image:
        if image.format != "WEBP" or not getattr(image, "is_animated", False):
            sys.exit("file is not an animated WebP")
        if image.size != CANVAS:
            sys.exit("canvas is %s, expected %s" % (image.size, CANVAS))
        if image.n_frames != len(durations):
            sys.exit("n_frames %d != expected %d" % (image.n_frames, len(durations)))
        if image.info.get("loop") != LOOP_COUNT:
            sys.exit("loop flag is %s, expected %d" % (image.info.get("loop"), LOOP_COUNT))
        decoded = []
        for index in range(image.n_frames):
            image.seek(index)
            image.load()
            decoded.append(image.info["duration"])
        if decoded != durations:
            sys.exit("decoded per-frame durations do not match what was written")
        image.seek(image.n_frames - 1)
        image.load()
        colours = {colour for _, colour in image.convert("RGBA").getcolors(1 << 16)}
        if ACCENT not in colours:
            sys.exit("no exact #C41E3A pixel in the last frame - lossless did not survive")
        if TRANSPARENT not in colours:
            sys.exit("last frame has no fully transparent pixel - the background is not clear")
    return riff


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(root / "assets" / "hero.webp"))
    parser.add_argument("--fast", action="store_true",
                        help="draft settings; refuses to write the committed asset")
    parser.add_argument("--check", action="store_true", help="verify an existing file and exit")
    args = parser.parse_args()
    out = Path(args.out)
    committed = (root / "assets" / "hero.webp").resolve()

    if not features.check_module("webp"):
        sys.exit("Pillow was built without WebP support")

    states = timeline()
    durations = [ms for _, ms in states]

    if args.check:
        verify(out, durations)
        print("ok: %s verified against %d expected frames" % (out, len(durations)))
        return

    if args.fast and out.resolve() == committed:
        sys.exit("--fast must not write the committed asset; pass --out to a scratch path")

    preflight(states)
    frames = [render(state) for state, _ in states]
    if len(durations) != len(frames):
        sys.exit("duration list and frame list are different lengths")

    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=LOOP_COUNT,
        lossless=True,
        quality=100,
        method=2 if args.fast else 6,
        minimize_size=not args.fast,
        background=TRANSPARENT,
    )

    verify(out, durations)
    size = out.stat().st_size
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    if size > SIZE_FAIL:
        sys.exit("%d bytes exceeds the %d byte ceiling" % (size, SIZE_FAIL))
    if size > SIZE_WARN:
        sys.stderr.write("warning: %d bytes is above the %d byte budget\n" % (size, SIZE_WARN))

    print("frames    %d" % len(frames))
    print("duration  %d ms" % sum(durations))
    print("size      %d bytes" % size)
    print("sha256    %s" % digest)
    print("libwebp   %s" % features.version_module("webp"))
    print()
    print('<img src="assets/hero.webp?v=%s" width="%d" height="%d" alt="%s">'
          % (digest[:8], DISPLAY[0], DISPLAY[1], COPY["alt"]))


if __name__ == "__main__":
    main()
