"""Render the profile hero: a looping crimson alert-console sequence, as animated WebP.

Deterministic for a fixed (Pillow, libwebp, FreeType) triple. Pinned to pillow==12.3.0.
Run with no arguments to rewrite assets/hero.webp and print the markdown line to paste.
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

from PIL import Image, ImageChops, ImageDraw, ImageFont, features

COPY = {
    "alert": "[ALERT] severity=high vector=inbound conf=0.97",
    "recon": ("resolving identity ...", "correlating 6 sources ..."),
    "verdict": "verdict: human. cleared.",
    "name": "Tyler Komander",
    "statement": "IT and AI. Local-first tools, run on hardware I control.",
    "alt": "Tyler Komander - IT and AI. Local-first tools, run on hardware I control.",
}

THEME = {
    "bg": (0x0A, 0x0A, 0x0B),
    "accent": (0xC4, 0x1E, 0x3A),
    "text": (0xD7, 0xD7, 0xDB),
    "dim": (0x6E, 0x6E, 0x73),
    "name": (0xF2, 0xF2, 0xF4),
    "statement": (0x8A, 0x8A, 0x90),
}

CANVAS = (1600, 640)
DISPLAY = (800, 320)
PAD_X = 72
ROW_Y = (104, 179, 254, 329, 404)
NAME_Y = 530
STATEMENT_Y = 596
MAX_LINE_CHARS = 62
MIN_FRAME_MS = 30
DURATION_BAND_MS = (8000, 12000)
SIZE_WARN = 400 * 1024
SIZE_FAIL = 900 * 1024

FONT_DIR = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    " .,:;=()[]<>/-_?!"
)


@lru_cache(maxsize=None)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / name
    if not path.exists():
        sys.exit("missing font: " + str(path))
    return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)


BODY = ("consola.ttf", 52)
BOLD = ("consolab.ttf", 52)
NAME_FONT = ("consolab.ttf", 96)
STATEMENT_FONT = ("consola.ttf", 44)

CELL_W = round(font(*BODY).getlength("M"))
NAME_CELL_W = round(font(*NAME_FONT).getlength("M"))
CURSOR_W = CELL_W - 5
CURSOR_H = 58
CURSOR_RISE = 44

Row = tuple
EMPTY_ROW = ("", "text", BODY)


@dataclass(frozen=True)
class State:
    rows: tuple
    name: str
    statement: str
    cursor: tuple | None


def initial_state() -> State:
    return State(rows=tuple(EMPTY_ROW for _ in ROW_Y), name="", statement="", cursor=(0, 0))


def set_row(state: State, index: int, row) -> State:
    rows = list(state.rows)
    rows[index] = row
    return replace(state, rows=tuple(rows))


@dataclass(frozen=True)
class Blink:
    row: int
    cycles: tuple = (500, 400, 300)

    def expand(self, state):
        for i, ms in enumerate(self.cycles):
            cursor = (self.row, 0) if i % 2 == 0 else None
            state = replace(state, cursor=cursor)
            yield state, ms


@dataclass(frozen=True)
class Type:
    row: int
    key: str
    colour: str
    face: tuple
    ms_per_char: int
    chars_per_frame: int = 1

    def expand(self, state):
        text = COPY[self.key]
        step = self.chars_per_frame
        for end in range(step, len(text) + step, step):
            prefix = text[:end]
            state = set_row(state, self.row, (prefix, self.colour, self.face))
            state = replace(state, cursor=(self.row, len(prefix)))
            yield state, self.ms_per_char * step


@dataclass(frozen=True)
class Reveal:
    row: int
    index: int
    colour: str
    face: tuple
    ms: int

    def expand(self, state):
        text = COPY["recon"][self.index]
        state = set_row(state, self.row, (text, self.colour, self.face))
        state = replace(state, cursor=(self.row, len(text)))
        yield state, self.ms


@dataclass(frozen=True)
class TypeName:
    ms_per_char: int

    def expand(self, state):
        for end in range(1, len(COPY["name"]) + 1):
            state = replace(state, name=COPY["name"][:end])
            yield state, self.ms_per_char


@dataclass(frozen=True)
class RevealStatement:
    ms: int

    def expand(self, state):
        state = replace(state, statement=COPY["statement"], cursor=None)
        yield state, self.ms


@dataclass(frozen=True)
class Pause:
    ms: int

    def expand(self, state):
        yield state, self.ms


@dataclass(frozen=True)
class ClearUp:
    step_ms: int

    def expand(self, state):
        state = replace(state, statement="")
        yield state, self.step_ms
        state = replace(state, name="")
        yield state, self.step_ms
        for index in reversed(range(len(ROW_Y))):
            if state.rows[index][0]:
                state = set_row(state, index, EMPTY_ROW)
                yield state, self.step_ms


BEATS = (
    Blink(row=0),
    Type(row=0, key="alert", colour="text", face=BODY, ms_per_char=32),
    Pause(260),
    Reveal(row=1, index=0, colour="dim", face=BODY, ms=420),
    Reveal(row=2, index=1, colour="dim", face=BODY, ms=380),
    Pause(300),
    Type(row=4, key="verdict", colour="accent", face=BOLD, ms_per_char=42),
    Pause(500),
    TypeName(ms_per_char=55),
    RevealStatement(ms=400),
    Pause(2800),
    ClearUp(step_ms=70),
    Pause(300),
)


def timeline():
    state = initial_state()
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
    # A second pass on the rendered pixels, not just the state: typing a space changes the
    # state but not the image, and libwebp folds pixel-identical frames into their
    # predecessor, which would leave the written ANMF count short of the duration list.
    collapsed = []
    for state, ms in merged:
        digest = hashlib.sha1(render(state).tobytes()).digest()
        if collapsed and collapsed[-1][2] == digest:
            collapsed[-1] = (collapsed[-1][0], collapsed[-1][1] + ms, digest)
        else:
            collapsed.append((state, ms, digest))
    return [(state, ms) for state, ms, _ in collapsed]


@lru_cache(maxsize=1)
def base_image() -> Image.Image:
    return Image.new("RGB", CANVAS, THEME["bg"])


@lru_cache(maxsize=None)
def render(state: State) -> Image.Image:
    image = base_image().copy()
    draw = ImageDraw.Draw(image)
    for index, (text, colour, face) in enumerate(state.rows):
        baseline = ROW_Y[index]
        glyph_font = font(*face)
        for column, character in enumerate(text):
            draw.text(
                (PAD_X + column * CELL_W, baseline),
                character,
                font=glyph_font,
                fill=THEME[colour],
                anchor="ls",
            )
    if state.name:
        glyph_font = font(*NAME_FONT)
        for column, character in enumerate(state.name):
            draw.text(
                (PAD_X + column * NAME_CELL_W, NAME_Y),
                character,
                font=glyph_font,
                fill=THEME["name"],
                anchor="ls",
            )
    if state.statement:
        draw.text(
            (PAD_X, STATEMENT_Y),
            state.statement,
            font=font(*STATEMENT_FONT),
            fill=THEME["statement"],
            anchor="ls",
        )
    if state.cursor is not None:
        row, column = state.cursor
        x = PAD_X + column * CELL_W
        y = ROW_Y[row]
        draw.rectangle(
            [x, y - CURSOR_RISE, x + CURSOR_W, y + CURSOR_H - CURSOR_RISE],
            fill=THEME["accent"],
        )
    return image


def cursor_box(state: State):
    row, column = state.cursor
    x = PAD_X + column * CELL_W
    y = ROW_Y[row]
    return (x, y - CURSOR_RISE, x + CURSOR_W + 1, y + CURSOR_H - CURSOR_RISE + 1)


def preflight(states) -> None:
    for key, value in COPY.items():
        if key == "alt":
            continue
        lines = value if isinstance(value, tuple) else (value,)
        for line in lines:
            if len(line) > MAX_LINE_CHARS:
                sys.exit("copy line too long (%d > %d): %s" % (len(line), MAX_LINE_CHARS, key))
            stray = set(line) - ALLOWED
            if stray:
                sys.exit("copy has glyphs outside the allowed set %s: %s" % (sorted(stray), key))
    durations = [ms for _, ms in states]
    if min(durations) < MIN_FRAME_MS:
        sys.exit("frame duration below the %dms floor: %d" % (MIN_FRAME_MS, min(durations)))
    total = sum(durations)
    if not DURATION_BAND_MS[0] <= total <= DURATION_BAND_MS[1]:
        sys.exit("total duration %dms outside %s" % (total, DURATION_BAND_MS))
    first, last = render(states[0][0]), render(states[-1][0])
    diff = ImageChops.difference(first, last).getbbox()
    if diff is not None:
        box = cursor_box(states[0][0])
        inside = box[0] <= diff[0] and box[1] <= diff[1] and diff[2] <= box[2] and diff[3] <= box[3]
        if not inside:
            sys.exit("loop seam not clean: difference %s escapes cursor box %s" % (diff, box))


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
    if "VP8X" not in chunks or not chunks["VP8X"][0][0] & 0x02:
        sys.exit("VP8X animation flag not set - decoders would treat this as a still image")
    if "ANIM" not in chunks:
        sys.exit("no ANIM chunk")
    loop_count = struct.unpack("<H", chunks["ANIM"][0][4:6])[0]
    if loop_count != 0:
        sys.exit("ANIM loop count is %d, expected 0 (infinite)" % loop_count)
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
        if image.info.get("loop") != 0:
            sys.exit("loop flag is %s, expected 0" % image.info.get("loop"))
        decoded = []
        accent_seen = False
        for index in range(image.n_frames):
            image.seek(index)
            image.load()
            decoded.append(image.info["duration"])
            if not accent_seen:
                band = image.convert("RGB").crop((0, ROW_Y[4] - 52, CANVAS[0], ROW_Y[4] + 16))
                accent_seen = any(c == THEME["accent"] for _, c in band.getcolors(1 << 16))
        if decoded != durations:
            sys.exit("decoded per-frame durations do not match what was written")
        if not accent_seen:
            sys.exit("no exact #C41E3A pixel in the verdict row - lossless did not survive")
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
        loop=0,
        lossless=True,
        quality=100,
        method=2 if args.fast else 6,
        minimize_size=not args.fast,
        background=THEME["bg"] + (255,),
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
