/******************************************************************************/
// Free implementation of Bullfrog's Dungeon Keeper strategy game.
/******************************************************************************/
/** @file sm64_mario.c
 *     Drive a possessed creature with Super Mario 64 physics via libsm64.
 * @par Purpose:
 *     Mario replaces the creature's movement while possessed: KeeperFX keys
 *     become joystick/buttons, the map around Mario becomes SM64 collision,
 *     and Mario's position is copied back into the creature every turn.
 * @par Comment:
 *     Single player only - libsm64 physics is float based and would desync
 *     network games. sm64.dll is loaded at runtime, so builds don't need it.
 * @par  Copying and copyrights:
 *     This program is free software; you can redistribute it and/or modify
 *     it under the terms of the GNU General Public License as published by
 *     the Free Software Foundation; either version 2 of the License, or
 *     (at your option) any later version.
 */
/******************************************************************************/
#include "pre_inc.h"
#include "sm64_mario.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <SDL3/SDL.h>
#include "libsm64.h"

#include "game_legacy.h"
#include "map_columns.h"
#include "map_data.h"
#include "bflib_planar.h"
#include "creature_control.h"
#include "engine_camera.h"
#include "packets.h"
#include "thing_data.h"
#include "thing_navigate.h"
#include "post_inc.h"

#ifdef __cplusplus
extern "C" {
#endif
/******************************************************************************/
#define SM64_ROM_FILE "sm64.us.z64"
#define SM64_ROM_SIZE (8 * 1024 * 1024)
#ifdef _WIN32
#define SM64_LIB_FILE "sm64.dll"
#else
#define SM64_LIB_FILE "libsm64.so"
#endif

// ponytail: hand-tuned so Mario (~160 units tall) matches an imp or dwarf (both ~256 coords); calibrate in game
#define SM64_SCALE 1.6f
// Collision is loaded only this many subtiles around an origin near Mario:
// libsm64 scans every static surface on each query, and SM64 space ends at +-8192.
#define SM64_WINDOW_STL 8
#define SM64_WINDOW_CELLS ((2 * SM64_WINDOW_STL + 1) * (2 * SM64_WINDOW_STL + 1))
// Invisible ceiling at the top of standard walls, so Mario can't climb onto them.
#define SM64_CEILING_HEIGHT (5 * COORD_PER_STL)
#define SM64_TICKS_PER_SECOND 30
#define SM64_TERRAIN_STONE 1
#define SM64_FULL_HEALTH 0x880
// ponytail: fixed chase distance; make it follow the zoom keys if people want to adjust it
#define SM64_CHASE_DISTANCE 700
/******************************************************************************/
static void (*p_global_init)(const uint8_t *rom, uint8_t *outTexture);
static void (*p_static_surfaces_load)(const struct SM64Surface *surfaceArray, uint32_t numSurfaces);
static int32_t (*p_mario_create)(float x, float y, float z);
static void (*p_mario_tick)(int32_t marioId, const struct SM64MarioInputs *inputs, struct SM64MarioState *outState, struct SM64MarioGeometryBuffers *outBuffers);
static void (*p_mario_delete)(int32_t marioId);
static void (*p_set_mario_position)(int32_t marioId, float x, float y, float z);
static void (*p_set_mario_health)(int32_t marioId, uint16_t health);
static void (*p_audio_init)(const uint8_t *rom);
static uint32_t (*p_audio_tick)(uint32_t numQueuedSamples, uint32_t numDesiredSamples, int16_t *audio_buffer);

static TbBool sm64_ready;
static int32_t mario_id = -1;
static ThingIndex mario_thing;
static uint32_t mario_input;
static int tick_accum;
static struct SM64MarioState mario_state;
static MapSubtlCoord origin_stl_x;
static MapSubtlCoord origin_stl_y;
static GameTurn last_rebuild_turn;
static short mario_face_angle;

static uint8_t mario_texture[4 * SM64_TEXTURE_WIDTH * SM64_TEXTURE_HEIGHT];
static float geo_position[9 * SM64_GEO_MAX_TRIANGLES];
static float geo_normal[9 * SM64_GEO_MAX_TRIANGLES];
static float geo_color[9 * SM64_GEO_MAX_TRIANGLES];
static float geo_uv[6 * SM64_GEO_MAX_TRIANGLES];
static struct SM64MarioGeometryBuffers mario_geometry = { geo_position, geo_normal, geo_color, geo_uv, 0 };

// Per cell at most: floor, ceiling and 4 walls, 2 triangles each
static struct SM64Surface surfaces[SM64_WINDOW_CELLS * 12];
static uint32_t surface_count;
/******************************************************************************/
/** SDL pulls Mario's voice and footsteps straight out of libsm64's own sound engine. */
static void SDLCALL mario_audio_callback(void *userdata, SDL_AudioStream *stream, int additional_amount, int total_amount)
{
    // ponytail: libsm64's demo also ticks audio off the game thread; its sound queue isn't locked
    int16_t buf[544 * 2 * 2];
    while (additional_amount > 0)
    {
        const uint32_t queued_frames = SDL_GetAudioStreamQueued(stream) / 4;
        // Each tick yields two blocks of stereo 16-bit frames
        const int frames = 2 * p_audio_tick(queued_frames, 1100, buf);
        if (frames <= 0)
            break;
        SDL_PutAudioStreamData(stream, buf, frames * 4);
        additional_amount -= frames * 4;
    }
}

static void start_mario_audio(const uint8_t *rom)
{
    p_audio_init(rom);
    const SDL_AudioSpec spec = { SDL_AUDIO_S16, 2, 32000 };
    // ponytail: own stream at fixed volume; route through the game's sound volume if it's too loud
    SDL_AudioStream *stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, mario_audio_callback, NULL);
    if (stream == NULL)
    {
        WARNLOG("Mario will be silent: %s", SDL_GetError());
        return;
    }
    SDL_ResumeAudioStreamDevice(stream);
}

static const char *sm64_load(void)
{
    if (sm64_ready)
        return NULL;
    SDL_SharedObject *lib = SDL_LoadObject(SM64_LIB_FILE);
    if (lib == NULL)
        return "Cannot load " SM64_LIB_FILE;
    // POSIX-style cast; function pointers have no portable cast from SDL_FunctionPointer otherwise
    *(void **)&p_global_init = (void *)SDL_LoadFunction(lib, "sm64_global_init");
    *(void **)&p_static_surfaces_load = (void *)SDL_LoadFunction(lib, "sm64_static_surfaces_load");
    *(void **)&p_mario_create = (void *)SDL_LoadFunction(lib, "sm64_mario_create");
    *(void **)&p_mario_tick = (void *)SDL_LoadFunction(lib, "sm64_mario_tick");
    *(void **)&p_mario_delete = (void *)SDL_LoadFunction(lib, "sm64_mario_delete");
    *(void **)&p_set_mario_position = (void *)SDL_LoadFunction(lib, "sm64_set_mario_position");
    *(void **)&p_set_mario_health = (void *)SDL_LoadFunction(lib, "sm64_set_mario_health");
    *(void **)&p_audio_init = (void *)SDL_LoadFunction(lib, "sm64_audio_init");
    *(void **)&p_audio_tick = (void *)SDL_LoadFunction(lib, "sm64_audio_tick");
    if (!p_global_init || !p_static_surfaces_load || !p_mario_create || !p_mario_tick
     || !p_mario_delete || !p_set_mario_position || !p_set_mario_health || !p_audio_init || !p_audio_tick)
    {
        SDL_UnloadObject(lib);
        return SM64_LIB_FILE " is missing functions, rebuild libsm64";
    }
    FILE *f = fopen(SM64_ROM_FILE, "rb");
    if (f == NULL)
        return "Cannot open " SM64_ROM_FILE;
    // libsm64 reads from the ROM during init only, but keeping it costs nothing worth saving
    uint8_t *rom = (uint8_t *)malloc(SM64_ROM_SIZE + 1);
    size_t len = (rom != NULL) ? fread(rom, 1, SM64_ROM_SIZE + 1, f) : 0;
    fclose(f);
    // Big-endian US ROM has its internal name at 0x20; .n64/.v64 byte orders won't match
    if ((len != SM64_ROM_SIZE) || (memcmp(rom + 0x20, "SUPER MARIO 64", 14) != 0))
    {
        free(rom);
        return SM64_ROM_FILE " is not a US Super Mario 64 ROM in .z64 byte order";
    }
    p_global_init(rom, mario_texture);
    start_mario_audio(rom);
    sm64_ready = true;
    JUSTLOG("libsm64 initialized");
    return NULL;
}

static void to_sm64(const struct Coord3d *pos, float out[3])
{
    // KeeperFX: x east, y south, z up. SM64: x east, y up, z south.
    out[0] = (pos->x.val - subtile_coord(origin_stl_x, 0)) / SM64_SCALE;
    out[1] = pos->z.val / SM64_SCALE;
    out[2] = (pos->y.val - subtile_coord(origin_stl_y, 0)) / SM64_SCALE;
}

/** Adds a planar quad, wound so its SM64 normal points along (want_x,want_y,want_z) in KeeperFX axes. */
static void add_quad(const struct Coord3d q[4], int want_x, int want_y, int want_z)
{
    int32_t v[4][3];
    for (int i = 0; i < 4; i++)
    {
        float f[3];
        to_sm64(&q[i], f);
        for (int k = 0; k < 3; k++)
            v[i][k] = lroundf(f[k]);
    }
    // Same normal formula as SM64's surface loading: (v2-v1) x (v3-v2)
    int64_t nx = (int64_t)(v[1][1] - v[0][1]) * (v[2][2] - v[1][2]) - (int64_t)(v[1][2] - v[0][2]) * (v[2][1] - v[1][1]);
    int64_t ny = (int64_t)(v[1][2] - v[0][2]) * (v[2][0] - v[1][0]) - (int64_t)(v[1][0] - v[0][0]) * (v[2][2] - v[1][2]);
    int64_t nz = (int64_t)(v[1][0] - v[0][0]) * (v[2][1] - v[1][1]) - (int64_t)(v[1][1] - v[0][1]) * (v[2][0] - v[1][0]);
    const int order[2][4] = {{0, 1, 2, 3}, {0, 3, 2, 1}};
    const int *o = order[(nx * want_x + ny * want_z + nz * want_y) < 0];
    for (int t = 0; t < 2; t++)
    {
        struct SM64Surface *s = &surfaces[surface_count++];
        s->type = 0; // SURFACE_DEFAULT
        s->force = 0;
        s->terrain = SM64_TERRAIN_STONE;
        memcpy(s->vertices[0], v[o[0]], sizeof(v[0]));
        memcpy(s->vertices[1], v[o[t + 1]], sizeof(v[0]));
        memcpy(s->vertices[2], v[o[t + 2]], sizeof(v[0]));
    }
}

static void set_coord(struct Coord3d *c, MapCoord x, MapCoord y, MapCoord z)
{
    c->x.val = x;
    c->y.val = y;
    c->z.val = z;
}

static void rebuild_collision(void)
{
    static const int dirs[4][2] = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};
    surface_count = 0;
    for (MapSubtlCoord sy = origin_stl_y - SM64_WINDOW_STL; sy <= origin_stl_y + SM64_WINDOW_STL; sy++)
    {
        for (MapSubtlCoord sx = origin_stl_x - SM64_WINDOW_STL; sx <= origin_stl_x + SM64_WINDOW_STL; sx++)
        {
            if (subtile_coords_invalid(sx, sy))
                continue;
            const MapCoord h = min(get_floor_height(sx, sy), SM64_CEILING_HEIGHT);
            const MapCoord x0 = subtile_coord(sx, 0), x1 = subtile_coord(sx + 1, 0);
            const MapCoord y0 = subtile_coord(sy, 0), y1 = subtile_coord(sy + 1, 0);
            struct Coord3d q[4];
            if (h < SM64_CEILING_HEIGHT)
            {
                set_coord(&q[0], x0, y0, h); set_coord(&q[1], x1, y0, h);
                set_coord(&q[2], x1, y1, h); set_coord(&q[3], x0, y1, h);
                add_quad(q, 0, 0, 1);
                for (int i = 0; i < 4; i++)
                    q[i].z.val = SM64_CEILING_HEIGHT;
                add_quad(q, 0, 0, -1);
            }
            // Wall from this cell's floor down to each lower neighbour, facing it.
            // Edges of the window get no walls; SM64 already stops Mario where floor ends.
            for (int d = 0; d < 4; d++)
            {
                const MapSubtlCoord nx = sx + dirs[d][0], ny = sy + dirs[d][1];
                if (subtile_coords_invalid(nx, ny) || (abs(nx - origin_stl_x) > SM64_WINDOW_STL) || (abs(ny - origin_stl_y) > SM64_WINDOW_STL))
                    continue;
                const MapCoord nh = get_floor_height(nx, ny);
                if (nh >= h)
                    continue;
                // Shared edge between the two cells
                const MapCoord ex0 = (dirs[d][0] > 0) ? x1 : x0, ex1 = (dirs[d][0] < 0) ? x0 : x1;
                const MapCoord ey0 = (dirs[d][1] > 0) ? y1 : y0, ey1 = (dirs[d][1] < 0) ? y0 : y1;
                set_coord(&q[0], ex0, ey0, nh); set_coord(&q[1], ex1, ey1, nh);
                set_coord(&q[2], ex1, ey1, h);  set_coord(&q[3], ex0, ey0, h);
                add_quad(q, dirs[d][0], dirs[d][1], 0);
            }
        }
    }
    p_static_surfaces_load(surfaces, surface_count);
    last_rebuild_turn = get_gameturn();
}
/******************************************************************************/
const char *sm64_mario_start(struct Thing *thing)
{
    const char *err = sm64_load();
    if (err != NULL)
        return err;
    sm64_mario_stop();
    origin_stl_x = thing->mappos.x.stl.num;
    origin_stl_y = thing->mappos.y.stl.num;
    rebuild_collision();
    float p[3];
    to_sm64(&thing->mappos, p);
    mario_id = p_mario_create(p[0], p[1], p[2]);
    if (mario_id < 0)
        return "Mario found no floor to stand on here";
    memset(&mario_state, 0, sizeof(mario_state));
    memcpy(mario_state.position, p, sizeof(p));
    mario_thing = thing->index;
    mario_face_angle = thing->move_angle_xy;
    mario_input = 0;
    tick_accum = 0;
    return NULL;
}

void sm64_mario_stop(void)
{
    if (mario_id >= 0)
        p_mario_delete(mario_id);
    mario_id = -1;
    mario_thing = 0;
}

TbBool sm64_mario_controls(const struct Thing *thing)
{
    // Thing indexes get reused, so a stale index must not grab a new creature
    return (mario_id >= 0) && (thing->index == mario_thing) && flag_is_set(thing->alloc_flags, TAlF_IsControlled);
}

void sm64_mario_set_input(uint32_t control_flags)
{
    mario_input = control_flags;
}

void sm64_mario_update(struct Thing *thing)
{
    const MapSubtlCoord stl_x = thing->mappos.x.stl.num;
    const MapSubtlCoord stl_y = thing->mappos.y.stl.num;
    if ((abs(stl_x - origin_stl_x) > SM64_WINDOW_STL / 2) || (abs(stl_y - origin_stl_y) > SM64_WINDOW_STL / 2))
    {
        // Re-centre; shift Mario by the exact origin delta to keep his sub-unit position
        mario_state.position[0] -= (stl_x - origin_stl_x) * COORD_PER_STL / SM64_SCALE;
        mario_state.position[2] -= (stl_y - origin_stl_y) * COORD_PER_STL / SM64_SCALE;
        origin_stl_x = stl_x;
        origin_stl_y = stl_y;
        rebuild_collision();
        p_set_mario_position(mario_id, mario_state.position[0], mario_state.position[1], mario_state.position[2]);
    } else
    // ponytail: periodic rebuild picks up digging/claiming; hook slab changes if a 1s lag matters
    if (get_gameturn() - last_rebuild_turn >= (GameTurn)turns_per_second)
    {
        rebuild_collision();
    }

    struct SM64MarioInputs in = {0};
    const float yaw = thing->move_angle_xy * (2.0f * (float)M_PI / DEGREES_360);
    // Camera looks where the creature faces; KeeperFX forward is (+sin, -cos)
    in.camLookX = sinf(yaw);
    in.camLookZ = -cosf(yaw);
    // libsm64 stick: right is +x, up is -y
    float sx = ((mario_input & PCtr_MoveRight) != 0) - ((mario_input & PCtr_MoveLeft) != 0);
    float sy = ((mario_input & PCtr_MoveDown) != 0) - ((mario_input & PCtr_MoveUp) != 0);
    if ((sx != 0) && (sy != 0))
    {
        sx *= (float)M_SQRT1_2;
        sy *= (float)M_SQRT1_2;
    }
    in.stickX = sx;
    in.stickY = sy;
    in.buttonA = (mario_input & PCtr_Ascend) != 0;
    in.buttonB = (mario_input & (PCtr_LBtnClick | PCtr_LBtnHeld)) != 0;
    in.buttonZ = (mario_input & PCtr_Descend) != 0;
    // Packets refill this every turn; frozen creatures get none, so Mario must stop
    mario_input = 0;

    // SM64 runs at 30Hz, the game at turns_per_second
    for (tick_accum += SM64_TICKS_PER_SECOND; tick_accum >= turns_per_second; tick_accum -= turns_per_second)
    {
        // ponytail: Mario never dies on his own; creature health still applies. Map damage across if wanted.
        p_set_mario_health(mario_id, SM64_FULL_HEALTH);
        p_mario_tick(mario_id, &in, &mario_state, &mario_geometry);
    }

    struct Coord3d pos;
    pos.x.val = clamp(subtile_coord(origin_stl_x, 0) + lroundf(mario_state.position[0] * SM64_SCALE), 0, subtile_coord(game.map_subtiles_x, 0) - 1);
    pos.y.val = clamp(subtile_coord(origin_stl_y, 0) + lroundf(mario_state.position[2] * SM64_SCALE), 0, subtile_coord(game.map_subtiles_y, 0) - 1);
    pos.z.val = max(lroundf(mario_state.position[1] * SM64_SCALE), 0);
    thing->velocity.x.val = 0;
    thing->velocity.y.val = 0;
    thing->velocity.z.val = 0;
    // The walk animation is paced by distance travelled
    struct CreatureControl *cctrl = creature_control_get_from_thing(thing);
    cctrl->distance_to_destination = get_2d_distance(&thing->mappos, &pos);
    // SM64 yaw 0 faces +z (south) and turns towards +x; KeeperFX 0 faces north and turns towards east
    mario_face_angle = (DEGREES_180 - (int)(mario_state.faceAngle * (DEGREES_180 / (float)M_PI))) & ANGLE_MASK;
    move_thing_in_map(thing, &pos);
}

short sm64_mario_display_angle(const struct Thing *thing)
{
    return sm64_mario_controls(thing) ? mario_face_angle : thing->move_angle_xy;
}

int sm64_mario_camera_distance(const struct Thing *thing, int eye_height)
{
    // Back off from the creature until the next step would put the camera inside a wall
    const MapCoord cam_z = thing->mappos.z.val + eye_height;
    int dist;
    for (dist = 90; dist < SM64_CHASE_DISTANCE; dist += 32)
    {
        const MapCoord x = move_coord_with_angle_x(thing->mappos.x.val, -(dist + 32), thing->move_angle_xy);
        const MapCoord y = move_coord_with_angle_y(thing->mappos.y.val, -(dist + 32), thing->move_angle_xy);
        if (subtile_coords_invalid(coord_subtile(x), coord_subtile(y)) || (get_floor_height(coord_subtile(x), coord_subtile(y)) >= cam_z))
            break;
    }
    return dist;
}
/******************************************************************************/
#ifdef __cplusplus
}
#endif
