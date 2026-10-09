# Mario imps — Super Mario 64 in KeeperFX

This fork turns your imps into Mario from Super Mario 64, using
[libsm64](https://github.com/libsm64/libsm64):

- **Possess an imp and you play as Mario**, in first person, with his real
  physics: run, jump (double, triple, long jump, backflip), crouch, punch.
  The slide music from Super Mario 64 plays while you're him.
- **Every imp looks and sounds like Mario**: his animations (walking,
  digging, claiming, sleeping, getting hit, dying), his portrait and hand
  icon, and his voice, including "yah! wah! hoo!" on every pickaxe hit.

Nothing from Super Mario 64 is in this repository. Mario's model,
animations, voice and music come from **your own copy** of the game's ROM,
at build time (the sprites and sounds) and at runtime (the physics, voice
and music).

> **Not affiliated with Nintendo, Bullfrog, EA or the KeeperFX team.**
> Don't share your ROM, and don't share the generated `mods/mario_imp`
> folder: it's made from Nintendo's assets.

---

## What you need

- A working KeeperFX install with the original Dungeon Keeper files
  (see `docs/files_required_from_original_dk.txt`).
- A **Super Mario 64 (USA)** ROM in `.z64` byte order (8 MB; the file
  starts with the internal name `SUPER MARIO 64` at offset 0x20).
- [Docker](https://www.docker.com/) to build. Everything builds inside a
  container with the same MinGW toolchain as KeeperFX's CI, so this works
  from Linux and macOS (including Apple Silicon).

---

## 1. Build the game

```bash
docker build -t kfx-mingw -f build/docker/mingw.Dockerfile build/docker
docker run --rm -v "$PWD":/src kfx-mingw sh -c \
  'cmake -S . -B out/docker-mingw -G Ninja -DCMAKE_TOOLCHAIN_FILE=build/cmake/toolchains/mingw32.cmake -DCMAKE_BUILD_TYPE=Release && cmake --build out/docker-mingw'
```

On macOS the icon tool KeeperFX uses has no Mac build; create the icon once
before building (any `.ico` works):

```bash
python3 -c "from PIL import Image; Image.open('res/keeperfx_icon256-24bpp.png').save('res/keeperfx_icon.ico', sizes=[(256,256),(64,64),(32,32),(16,16)])"
```

Output: `out/docker-mingw/keeperfx.exe`, plus the SDL3 DLLs it needs under
`out/docker-mingw/deps/*/*/i686-w64-mingw32/bin/`.

## 2. Build libsm64 and the Mario mod

```bash
build/docker/sm64.sh path/to/sm64.us.z64
```

This builds `out/libsm64/dist/sm64.dll` (libsm64, pinned to a known
commit), runs two quick checks that need no ROM, and generates
`out/mods/mario_imp/` from your ROM. It copies the ROM into the git-ignored
`out/` folder while it runs and needs internet access the first time.

## 3. Install into your game folder

Copy into your KeeperFX folder:

| From | To |
|---|---|
| `out/docker-mingw/keeperfx.exe` | `keeperfx.exe` (replace) |
| the three `SDL3*.dll` (i686 builds) | next to `keeperfx.exe` |
| `config/creatrs/*.cfg` | `creatrs/` (they match this build) |
| `config/fxdata/*.cfg`, `*.toml`, `*.txt` | `fxdata/` |
| `out/libsm64/dist/sm64.dll` | next to `keeperfx.exe` |
| your ROM | `sm64.us.z64`, next to `keeperfx.exe` |
| `out/mods/mario_imp/` | `mods/mario_imp/` |

Then:

- In `keeperfx.cfg`, add `MARIO_IMPS=ON`.
- In `mods/load_order.cfg` (create it if needed):

  ```
  [after_base]
  mario_imp
  ```

The mod alone (step 2) only changes how imps look and sound; playing as
Mario needs the exe, `sm64.dll`, the ROM and `MARIO_IMPS=ON`.

## 4. Play

Start a single-player level and possess one of your imps.

| Key | Mario |
|---|---|
| Movement keys | run, relative to where you look |
| Mouse | look |
| **Ascend** (default `X`) | jump — again on landing for double/triple jumps |
| **Descend** (default `Z`) | crouch; while running + jump: long jump; standing + jump: backflip |
| Left click | punch |

Leaving the imp turns Mario off and brings the dungeon music back. With
cheat mode on (start the game with `-alex`), `!mario` in the chat box
(press **Enter**) turns Mario on or off for any creature you're possessing.

### macOS

KeeperFX is a Windows program; on a Mac run it with
[CrossOver](https://www.codeweavers.com/crossover) (Homebrew's free Wine
casks are disabled). Create a Windows 10 bottle, then **Run Command**:
`"/path/to/KeeperFX/keeperfx.exe" -alex` — the argument goes after the
quoted path, not in "Environment variables". The Dungeon Keeper files can
come from Steam without Wine, via
`steamcmd +@sSteamCmdForcePlatformType windows +login <you> +app_update 1996630 validate +quit`.

---

## How it works

| Piece | Where |
|---|---|
| Mario in the game: loads `sm64.dll` at runtime with SDL (so normal builds don't need it), turns the map around Mario into SM64 collision, maps keys to his controller, copies his position into the possessed creature, plays his voice and music | `src/sm64_mario.c` |
| Hooks: possession start/end, creature movement, input, `MARIO_IMPS`, `!mario` | `src/thing_creature.c`, `src/player_instances.c`, `src/packets.c`, `src/front_input.c`, `src/config_keeperfx.c`, `src/console_cmd.c` |
| libsm64's header (CC0) | `src/libsm64.h` |
| Mod generator: renders Mario's animations and icons with libsm64 into KeeperFX sprites, records his voice into WAVs, writes the mod's configs | `tools/sm64/make_mario_mod.py` |
| Builds `sm64.dll`, runs the checks, runs the generator | `build/docker/sm64.sh` |
| Checks: collision as libsm64 sees it; sprite framing | `tests/sm64/` |

Things worth knowing if you change it:

- `sm64.dll` must be built with SSE2 math (`sm64.sh` does). 32-bit x86
  defaults to x87, which Rosetta emulates very slowly; under CrossOver on
  Apple Silicon Mario's physics took ~30 ms a game turn instead of ~0.1 ms.
- libsm64's sound engine and Mario's physics share state, so both run on
  the game thread; the audio is topped up each game turn.
- Collision is loaded for a window of 17×17 subtiles around Mario and
  rebuilt when he moves away or every second (to pick up digging): libsm64
  checks every triangle on every query, and SM64 space ends at ±8192.
- Sizes and angles that were tuned by eye: `SM64_SCALE` in
  `src/sm64_mario.c`; `TD_PIXELS`, `TD_PITCH` in the generator.

## Known limitations

- Single player only. libsm64's physics uses floating point and would
  desync multiplayer games; `!mario` and `MARIO_IMPS` refuse to run there.
- Doors and other creatures aren't solid to Mario.
- Mario doesn't take damage himself; the imp's own health still applies.
- Digging shows up in Mario's collision up to a second late.
- Saving or loading while Mario is active isn't handled; leave the imp first.
- Mario's voice and music follow the sound-effects volume, not the music
  volume (libsm64 mixes them into one stream).
- First person only.

## Credits

- [libsm64](https://github.com/libsm64/libsm64) (CC0), built on the
  [Super Mario 64 decompilation](https://github.com/n64decomp/sm64).
- [KeeperFX](https://github.com/dkfans/keeperfx) and its contributors.
- Super Mario 64 and Mario are © Nintendo. Dungeon Keeper is © Electronic
  Arts.
