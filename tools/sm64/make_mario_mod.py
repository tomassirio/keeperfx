#!/usr/bin/env python3
"""Build a KeeperFX mod that turns imps into Mario, from your own SM64 ROM.

Mario's animations are rendered with libsm64 into sprite frames and his voice
lines are recorded through libsm64's sound engine into WAVs. Nothing from the
ROM is kept in the repository; run it locally (build/docker/sm64.sh does):

    make_mario_mod.py --lib libsm64.so --rom sm64.us.z64 --out out/mods

Then copy out/mods/mario_imp into the game's mods/ folder and list
"mario_imp" under [after_base] in mods/load_order.cfg.
"""
import argparse
import ctypes
import io
import json
import math
import os
import wave
import zipfile
from ctypes import POINTER, c_float, c_int16, c_int32, c_uint8, c_uint16, c_uint32

from PIL import Image, ImageDraw

MOD_NAME = "mario_imp"
MAX_TRIS = 1024
TEX_W, TEX_H = 64 * 11, 64
MARIO_HEIGHT = 160.0  # SM64 units, feet to cap

# ponytail: hand-picked sprite sizes and camera pitch; compare with real dwarves in game and tune
TD_PIXELS = 64        # Mario's height in top-down (isometric) sprites
FP_PIXELS = 64        # ... and in first-person/perspective sprites
TD_PITCH = 35.0       # degrees the camera looks down at the sprite
FP_PITCH = 10.0
SUPERSAMPLE = 4
MARIO_REACH = 50.0    # SM64 units Mario's toes/fists reach towards the camera; tilted below the feet row

# From libsm64 src/decomp/include/sm64.h
ACT_IDLE = 0x0C400201
ACT_SLEEPING = 0x0C000203
ACT_PUNCHING = 0x00800380
ACT_BACKWARD_GROUND_KB = 0x00020462
ACT_HARD_BACKWARD_GROUND_KB = 0x00020460
ACT_STANDING_DEATH = 0x00021311
ACT_STAR_DANCE_NO_EXIT = 0x00001307

# Each animation: optional action to force, optional stick input, ticks to settle, frames, ticks per frame
ANIMATIONS = {
    "STAND":   dict(action=ACT_IDLE, frames=12, step=4),
    "WALK":    dict(stick=(0.0, -0.45), warmup=20, frames=12, step=2),
    "PUNCH":   dict(action=ACT_PUNCHING, frames=8, step=1),
    "HIT":     dict(action=ACT_BACKWARD_GROUND_KB, frames=10, step=2),
    "SLAPPED": dict(action=ACT_HARD_BACKWARD_GROUND_KB, frames=12, step=2),
    "DEATH":   dict(action=ACT_STANDING_DEATH, frames=16, step=4),
    "SLEEP":   dict(action=ACT_SLEEPING, warmup=10, frames=12, step=4),
    "DANCE":   dict(action=ACT_STAR_DANCE_NO_EXIT, frames=16, step=3),
}

# Imp [sprites] slot -> animation above (slots not listed keep the imp's own)
IMP_SPRITES = {
    "Stand": "STAND", "Ambulate": "WALK", "Drag": "WALK",
    "Attack": "PUNCH", "RangedAttack": "PUNCH", "CastSpell": "PUNCH",
    "Dig": "PUNCH",                                   # digging and reinforcing walls
    "Smoke": "SLEEP", "Relax": "SLEEP", "Sleep": "SLEEP",
    "PrettyDance": "DANCE",                           # claiming floor
    "Celebrate": "DANCE", "Roar": "DANCE",
    "GotHit": "HIT", "PowerGrab": "HIT", "Scream": "HIT",
    "GotSlapped": "SLAPPED", "DropDead": "DEATH",
    "EatChicken": "STAND", "Piss": "STAND",
}

# Creature panel pictures, rendered from the STAND frame best_icon_pose() picks. Sizes match the imp's originals
# (gui1 #154 and gui2 #222/#223): slot -> (icon name, [(hi-res size, low-res size, share of Mario from the top)])
# Hand icons copy the imp's layout: a small head with a drop shadow low in the 30x50 canvas, top left
# empty for the level badge drawn over it; "icon + 1" is the same head greyed out (dead/disabled).
HEAD = 0.45  # share of Mario from the top that is head and shoulders
FACE = 0.42  # ... that is just his head, cap to chin
IMP_ICONS = {
    "QuerySymbol": ("MARIO_PORTRAIT", [((48, 48), (24, 24), HEAD)]),
    "HandSymbol":  ("MARIO_ICON", [((30, 50), (15, 25), "hand"),
                                   ((30, 50), (15, 25), "hand grey")]),
}


def sound_arg_load(bank, play_flags, sound_id, priority, flags2):
    # SOUND_ARG_LOAD from libsm64 src/decomp/include/audio_defines.h; low bit is SOUND_STATUS_STARTING
    return (bank << 28) | (play_flags << 24) | (sound_id << 16) | (priority << 8) | (flags2 << 4) | 1


def mario_voice(sound_id, priority=0x80):
    return sound_arg_load(2, 4, sound_id, priority, 8)


# Imp [sounds] slot -> Mario voice lines (one WAV per variant). Foot and Piss stay the imp's.
IMP_SOUNDS = {
    "Hit":     [mario_voice(0x0A, 0xFF), mario_voice(0x0B), mario_voice(0x05)],   # attacked, oof, uh
    "Happy":   [mario_voice(0x04)],                                               # yahoo
    "Sad":     [mario_voice(0x20)],                                               # mamma mia
    "Die":     [mario_voice(0x15, 0xFF), mario_voice(0x10, 0xC0)],                # dying, waaaooow
    "Hang":    [mario_voice(0x08, 0xC0)],                                         # whoa
    "Drop":    [mario_voice(0x0C)],                                               # here we go
    "Torture": [mario_voice(0x0A, 0xFF), mario_voice(0x14, 0xA0), mario_voice(0x10, 0xC0)],
    "Slap":    [mario_voice(0x0B)],                                               # oof
    "Fight":   [mario_voice(0x1E), mario_voice(0x24), mario_voice(0x1F)],         # punch yah, wah, hoo
}

# Digging isn't a creature sound. Each pickaxe hit plays the dig shot's DigHitSound, which the mod
# points at a new MARIO_DIG ([shot23] SHOT_DIG in magic.cfg), so other creatures smashing walls keep
# DIG_SPELL. Breaking through a block plays DIG_IMPACT, which only digging uses, so it's replaced by
# name (not by ID: DOOR_PLACE shares those IDs). Variants must be consecutive: the game picks id + random.
DIG_SOUNDS = {
    "MARIO_DIG":  [mario_voice(0x1E), mario_voice(0x24), mario_voice(0x1F),       # punch yah, wah, hoo
                   mario_voice(0x00), mario_voice(0x01), mario_voice(0x02)],      # jump yah, wah, hoo
    "DIG_IMPACT": [mario_voice(0x04), mario_voice(0x03), mario_voice(0x2B)],      # yahoo, hoohoo, yippee
}
DIG_SHOT_BLOCK = "shot23"  # SHOT_DIG in fxdata/magic.cfg


class SM64Surface(ctypes.Structure):
    _fields_ = [("type", c_int16), ("force", c_int16), ("terrain", c_uint16), ("vertices", (c_int32 * 3) * 3)]


class SM64MarioInputs(ctypes.Structure):
    _fields_ = [("camLookX", c_float), ("camLookZ", c_float), ("stickX", c_float), ("stickY", c_float),
                ("buttonA", c_uint8), ("buttonB", c_uint8), ("buttonZ", c_uint8)]


class SM64MarioState(ctypes.Structure):
    _fields_ = [("position", c_float * 3), ("velocity", c_float * 3), ("faceAngle", c_float),
                ("forwardVelocity", c_float), ("health", c_int16), ("action", c_uint32), ("animID", c_int32),
                ("animFrame", c_int16), ("flags", c_uint32), ("particleFlags", c_uint32), ("invincTimer", c_int16)]


class SM64MarioGeometryBuffers(ctypes.Structure):
    _fields_ = [("position", POINTER(c_float)), ("normal", POINTER(c_float)), ("color", POINTER(c_float)),
                ("uv", POINTER(c_float)), ("numTrianglesUsed", c_uint16)]


def load_lib(path):
    lib = ctypes.CDLL(path)
    sigs = {
        "sm64_global_init": (None, [ctypes.c_char_p, POINTER(c_uint8)]),
        "sm64_audio_init": (None, [ctypes.c_char_p]),
        "sm64_audio_tick": (c_uint32, [c_uint32, c_uint32, POINTER(c_int16)]),
        "sm64_play_sound_global": (None, [c_int32]),
        "sm64_static_surfaces_load": (None, [POINTER(SM64Surface), c_uint32]),
        "sm64_mario_create": (c_int32, [c_float, c_float, c_float]),
        "sm64_mario_tick": (None, [c_int32, POINTER(SM64MarioInputs), POINTER(SM64MarioState),
                                   POINTER(SM64MarioGeometryBuffers)]),
        "sm64_mario_delete": (None, [c_int32]),
        "sm64_set_mario_action": (None, [c_int32, c_uint32]),
    }
    for name, (restype, argtypes) in sigs.items():
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
    return lib


class Mario:
    def __init__(self, lib):
        self.lib = lib
        self.state = SM64MarioState()
        self.inputs = SM64MarioInputs(camLookX=0.0, camLookZ=-1.0)
        self.bufs = [(c_float * (9 * MAX_TRIS))() for _ in range(3)] + [(c_float * (6 * MAX_TRIS))()]
        self.geo = SM64MarioGeometryBuffers(*[ctypes.cast(b, POINTER(c_float)) for b in self.bufs], 0)
        self.id = lib.sm64_mario_create(0.0, 0.0, 0.0)
        if self.id < 0:
            raise RuntimeError("libsm64 could not create Mario")

    def tick(self):
        self.lib.sm64_mario_tick(self.id, ctypes.byref(self.inputs), ctypes.byref(self.state), ctypes.byref(self.geo))

    def triangles(self):
        """Triangles as ((pos, normal, color, uv) * 3), relative to Mario's feet, Mario facing +z."""
        pos, nrm, col, uv = self.bufs
        px, py, pz = self.state.position
        yaw = -self.state.faceAngle
        out = []
        for t in range(self.geo.numTrianglesUsed):
            tri = []
            for v in range(3):
                i = 9 * t + 3 * v
                p = rotate_y((pos[i] - px, pos[i + 1] - py, pos[i + 2] - pz), yaw)
                n = rotate_y((nrm[i], nrm[i + 1], nrm[i + 2]), yaw)
                j = 6 * t + 2 * v
                tri.append((p, n, (col[i], col[i + 1], col[i + 2]), (uv[j], uv[j + 1])))
            out.append(tri)
        return out

    def delete(self):
        self.lib.sm64_mario_delete(self.id)


def rotate_y(v, a):
    """Rotates about +y so that a direction facing +z with angle a ends up facing (sin a, cos a)."""
    x, y, z = v
    c, s = math.cos(a), math.sin(a)
    return (x * c + z * s, y, -x * s + z * c)


def render(tris, texture, yaw, pitch_deg, height_px):
    """Orthographic render, camera on +z looking at -z from pitch_deg above.
    Returns the image and the row Mario's feet stand on, centred horizontally."""
    pitch = math.radians(pitch_deg)
    cp, sp = math.cos(pitch), math.sin(pitch)
    scale = height_px * SUPERSAMPLE / MARIO_HEIGHT
    margin = math.ceil(MARIO_REACH * sp * height_px / MARIO_HEIGHT) + 1
    size = int(height_px * 1.6) + margin  # headroom for knockbacks and the star dance
    w = h = size * SUPERSAMPLE
    feet_row = h - margin * SUPERSAMPLE
    light = (-0.35, 0.75, 0.55)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    projected = []
    for tri in tris:
        pts, depth, rgb, tri_shade = [], 0.0, [0.0, 0.0, 0.0], 0.0
        for p, n, col, uv in tri:
            x, y, z = rotate_y(p, yaw)
            pts.append((w / 2 + x * scale, feet_row - (y * cp - z * sp) * scale))
            depth += z * cp + y * sp
            nx, ny, nz = rotate_y(n, yaw)
            shade = 0.45 + 0.55 * max(0.0, nx * light[0] + ny * light[1] + nz * light[2])
            tri_shade += shade / 3
            for k in range(3):
                rgb[k] += col[k] * shade / 3
        # libsm64 marks untextured triangles with uv >= 1
        uvs = [v[3] for v in tri]
        textured = sum(u for u, _ in uvs) / 3 < 1 and sum(v for _, v in uvs) / 3 < 1
        colour = tuple(min(255, int(c * 255)) for c in rgb)
        projected.append((depth, pts, colour, uvs if textured else None, tri_shade))
    for _, pts, colour, uvs, shade in sorted(projected, key=lambda t: t[0]):  # far first
        if uvs is None or not draw_textured(img, pts, uvs, texture, colour, shade):
            draw.polygon(pts, fill=colour + (255,))
    img = hard_alpha(img.resize((size, size), Image.BOX))
    return img, size - margin


def draw_textured(img, pts, uvs, texture, colour, shade):
    """Draws a triangle with the atlas blended over its vertex colour by the atlas alpha.
    The projection is orthographic, so an affine warp of the atlas is exact. False if degenerate."""
    left, top = int(min(x for x, _ in pts)), int(min(y for _, y in pts))
    right, bottom = int(max(x for x, _ in pts)) + 2, int(max(y for _, y in pts)) + 2
    (x0, y0), (x1, y1), (x2, y2) = [(x - left, y - top) for x, y in pts]
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(det) < 1e-6:
        return False
    # Affine map from patch pixel (X, Y) to atlas pixel, solved from the three corners
    coeffs = []
    for t0, t1, t2 in ([u * TEX_W for u, _ in uvs], [v * TEX_H for _, v in uvs]):
        a = ((t1 - t0) * (y2 - y0) - (t2 - t0) * (y1 - y0)) / det
        b = ((t2 - t0) * (x1 - x0) - (t1 - t0) * (x2 - x0)) / det
        coeffs += [a, b, t0 - a * x0 - b * y0]
    size = (right - left, bottom - top)
    tex = texture.transform(size, Image.AFFINE, coeffs, resample=Image.BILINEAR)
    lit = tex.convert("RGB").point(lambda c: min(255, int(c * shade)))
    patch = Image.composite(lit, Image.new("RGB", size, colour), tex.getchannel("A"))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon([(x0, y0), (x1, y1), (x2, y2)], fill=255)
    img.paste(patch, (left, top), mask)
    return True


def hard_alpha(img):
    """Palette sprites have no partial transparency."""
    img.putalpha(img.getchannel("A").point(lambda a: 255 if a >= 128 else 0))
    return img


def best_icon_pose(frames, texture):
    """The idle frame where Mario looks straight ahead with his eyes open. Rendered head-on, his
    less visible eye scores, so a blink or a turned head (one eye hidden) loses. His idle animation
    looks around and blinks, and not identically from run to run."""
    def is_iris(px):
        r, g, b, a = px
        return a and b > 120 and b > r + 40
    def weaker_eye(tris):
        img = render_icon(tris, texture, (48, 48), FACE, yaw_deg=0)
        halves = (img.crop((0, 0, 24, 48)), img.crop((24, 0, 48, 48)))
        return min(sum(map(is_iris, half.getdata())) for half in halves)
    return max(frames, key=weaker_eye)


def render_icon(tris, texture, size, share, yaw_deg=20):
    """Mario turned slightly towards the viewer, the top `share` of him fitted into size (w, h).
    "hand" / "hand grey": his face straight on, small and shadowed where the imp's hand icon has its head."""
    if share in ("hand", "hand grey"):
        # Where the imp's head sits in its 30x50 hand icon (gui2 #222), scaled for low-res
        s = size[0] / 30
        side, left, top, drop = round(17 * s), round(6 * s), round(23 * s), max(1, round(2 * s))
        face = render_icon(tris, texture, (side, side), FACE, yaw_deg=0)
        if share == "hand grey":
            face = Image.merge("RGBA", (*[face.convert("L")] * 3, face.getchannel("A")))
        shadow = Image.new("RGBA", face.size, (16, 12, 8, 255))
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        canvas.paste(shadow, (left + drop, top + drop), face.getchannel("A"))
        canvas.paste(face, (left, top), face.getchannel("A"))
        return canvas
    img, _ = render(tris, texture, math.radians(yaw_deg), FP_PITCH, 256)
    alpha = img.getchannel("A")
    left, top, right, bottom = alpha.getbbox()
    crop_h = (bottom - top) * share
    crop_w = crop_h * size[0] / size[1]
    # Centre on what's inside the crop (the head is off-centre when he's turned)
    rows = alpha.crop((0, top, img.width, round(top + min(crop_h, bottom - top)))).getbbox()
    cx = (rows[0] + rows[2]) / 2
    top -= (bottom - top) * max(share - 1, 0) / 2  # spread any spare room above and below
    img = img.crop((round(cx - crop_w / 2), round(top), round(cx + crop_w / 2), round(top + crop_h)))
    return hard_alpha(img.resize(size, Image.LANCZOS))


def capture(lib, recipe):
    mario = Mario(lib)
    try:
        if "stick" in recipe:
            mario.inputs.stickX, mario.inputs.stickY = recipe["stick"]
        if "action" in recipe:
            lib.sm64_set_mario_action(mario.id, recipe["action"])
        for _ in range(recipe.get("warmup", 0)):
            mario.tick()
        frames = []
        for _ in range(recipe["frames"]):
            for _ in range(recipe["step"]):
                mario.tick()
            frames.append(mario.triangles())
        return frames
    finally:
        mario.delete()


def record_sound(lib, sound_bits, seconds=2.0):
    lib.sm64_play_sound_global(sound_bits)
    buf = (c_int16 * (544 * 2 * 2))()
    pcm = bytearray()
    while len(pcm) < seconds * 32000 * 4:
        frames = 2 * lib.sm64_audio_tick(0, 1100, buf)
        pcm += bytes(buf)[:frames * 4]
    # Trim trailing silence so the game doesn't hold a voice slot for nothing
    samples = memoryview(pcm).cast("h")
    end = len(samples)
    while end > 0 and abs(samples[end - 1]) < 64:
        end -= 1
    end += (-end) % 2  # whole stereo frames
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(32000)
        wav.writeframes(samples[:end].tobytes())
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", required=True, help="native libsm64 shared library")
    ap.add_argument("--rom", required=True, help="Super Mario 64 US ROM, .z64 byte order")
    ap.add_argument("--out", required=True, help="directory to create the mod in")
    args = ap.parse_args()

    rom = open(args.rom, "rb").read()
    if len(rom) != 8 * 1024 * 1024 or rom[0x20:0x2E] != b"SUPER MARIO 64":
        raise SystemExit(f"{args.rom} is not a US Super Mario 64 ROM in .z64 byte order")
    lib = load_lib(args.lib)
    tex = (c_uint8 * (4 * TEX_W * TEX_H))()
    lib.sm64_global_init(rom, tex)
    lib.sm64_audio_init(rom)
    texture = Image.frombytes("RGBA", (TEX_W, TEX_H), bytes(tex))

    floor = (SM64Surface * 2)()
    for s, verts in zip(floor, [((-8000, 0, -8000), (-8000, 0, 8000), (8000, 0, 8000)),
                                ((-8000, 0, -8000), (8000, 0, 8000), (8000, 0, -8000))]):
        for i, v in enumerate(verts):
            s.vertices[i][:] = v
    lib.sm64_static_surfaces_load(floor, 2)

    mod = os.path.join(args.out, MOD_NAME)
    for sub in ("fxdata", "creatrs", "sound"):
        os.makedirs(os.path.join(mod, sub), exist_ok=True)

    sprites = []
    with zipfile.ZipFile(os.path.join(mod, "fxdata", MOD_NAME + ".zip"), "w", zipfile.ZIP_DEFLATED) as zf:
        for anim, recipe in ANIMATIONS.items():
            frames = capture(lib, recipe)
            if anim == "STAND":
                stand_pose = best_icon_pose(frames, texture)
            entry = {"name": "MARIO_" + anim, "rotatable": True}
            for view, pitch, px in (("td", TD_PITCH, TD_PIXELS), ("fp", FP_PITCH, FP_PIXELS)):
                dirs = []
                for d in range(5):  # front, front-right, right, back-right, back; the game mirrors the rest
                    files = []
                    for f, tris in enumerate(frames):
                        img, feet_row = render(tris, texture, math.radians(45 * d), pitch, px)
                        name = f"{anim.lower()}/{view}{d}_{f:02}.png"
                        data = io.BytesIO()
                        img.save(data, "PNG")
                        zf.writestr(name, data.getvalue())
                        files.append({"file": name, "offset_x": img.width // 2, "offset_y": feet_row})
                    dirs.append(files)
                entry[view] = dirs
            sprites.append(entry)
            print(f"rendered {entry['name']}: {len(frames)} frames x 5 directions")
        zf.writestr("sprites.json", json.dumps(sprites, indent=1))

        icons = []
        for name, variants in IMP_ICONS.values():
            entry = {"name": name, "file": [], "lowres": []}
            for k, (hires, lowres, share) in enumerate(variants):
                for key, size in (("file", hires), ("lowres", lowres)):
                    data = io.BytesIO()
                    render_icon(stand_pose, texture, size, share).save(data, "PNG")
                    path = f"icons/{name.lower()}{k}_{size[0]}x{size[1]}.png"
                    zf.writestr(path, data.getvalue())
                    entry[key].append(path)
            icons.append(entry)
        zf.writestr("icons.json", json.dumps(icons, indent=1))
        print(f"rendered {len(icons)} icons")

    cfg = ["; Generated by tools/sm64/make_mario_mod.py from your own ROM", "[sprites]"]
    cfg += [f"{slot} = MARIO_{anim}" for slot, anim in IMP_SPRITES.items()]
    cfg += [f"{slot} = {icon[0]}" for slot, icon in IMP_ICONS.items()]
    cfg += ["", "[sounds]"]
    for slot, voices in IMP_SOUNDS.items():
        for i, bits in enumerate(voices, 1):
            with open(os.path.join(mod, "sound", f"mario_{slot.lower()}{i}.wav"), "wb") as f:
                f.write(record_sound(lib, bits))
        # Numbered variants: mario_hit1.wav 3 -> mario_hit1, mario_hit2, mario_hit3
        cfg.append(f"{slot} = mario_{slot.lower()}1.wav {len(voices)}")
    with open(os.path.join(mod, "creatrs", "imp.cfg"), "w") as f:
        f.write("\n".join(cfg) + "\n")

    sounds_cfg = ["; Generated by tools/sm64/make_mario_mod.py from your own ROM", "[sounds]"]
    for name, voices in DIG_SOUNDS.items():
        stem = name.lower() if name.startswith("MARIO_") else "mario_" + name.lower()
        for i, bits in enumerate(voices, 1):
            with open(os.path.join(mod, "sound", f"{stem}{i}.wav"), "wb") as f:
                f.write(record_sound(lib, bits))
        sounds_cfg.append(f"{name} = {stem}1.wav {len(voices)}")
    with open(os.path.join(mod, "sounds.cfg"), "w") as f:
        f.write("\n".join(sounds_cfg) + "\n")
    with open(os.path.join(mod, "fxdata", "magic.cfg"), "w") as f:
        f.write(f"; Generated by tools/sm64/make_mario_mod.py\n[{DIG_SHOT_BLOCK}]\n"
                f"DigHitSound = MARIO_DIG {len(DIG_SOUNDS['MARIO_DIG'])}\n")
    print(f"recorded {sum(len(v) for v in IMP_SOUNDS.values()) + sum(len(v) for v in DIG_SOUNDS.values())} voice lines")
    print(f"mod written to {mod}")


if __name__ == "__main__":
    main()
