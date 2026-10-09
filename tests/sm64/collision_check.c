// Collision check for sm64_mario.c: runs the real rebuild_collision() on a fake map
// and asks libsm64 what it sees. Needs no ROM. Run: build/docker/sm64.sh
#include "sm64_mario.c"
#include <assert.h>
#include <stdarg.h>

struct Game game;
SDL_SharedObject *SDL_LoadObject(const char *f) { (void)f; return 0; }
SDL_FunctionPointer SDL_LoadFunction(SDL_SharedObject *l, const char *n) { (void)l; (void)n; return 0; }
void SDL_UnloadObject(SDL_SharedObject *l) { (void)l; }
int32_t turns_per_second = 20;
GameTurn get_gameturn(void) { return 0; }
int LbJustLog(const char *fmt, ...) { (void)fmt; return 0; }
void move_thing_in_map_f(struct Thing *t, const struct Coord3d *p, const char *f) { (void)t; (void)p; (void)f; }
TbBool subtile_coords_invalid(MapSubtlCoord x, MapSubtlCoord y) { return x < 0 || y < 0 || x > game.map_subtiles_x || y > game.map_subtiles_y; }

// Floor 256 everywhere, a wall (1280) at subtile x=12, a pit (0) at x=8
MapCoord get_floor_height(MapSubtlCoord x, MapSubtlCoord y) { (void)y; return x == 12 ? 1280 : x == 8 ? 0 : 256; }

#define STL(n) ((n) * COORD_PER_STL / SM64_SCALE)   // subtile edge relative to origin 10
int main(void)
{
    game.map_subtiles_x = game.map_subtiles_y = 255;
    p_static_surfaces_load = sm64_static_surfaces_load;
    origin_stl_x = 10; origin_stl_y = 10;
    rebuild_collision();
    printf("surfaces: %u (max %u)\n", surface_count, (unsigned)(sizeof(surfaces)/sizeof(surfaces[0])));

    float mid = 0.5f * COORD_PER_STL / SM64_SCALE;  // centre of origin subtile
    float f = sm64_surface_find_floor_height(mid, 500, mid);
    printf("floor at origin: %.1f (want %.1f)\n", f, 256 / SM64_SCALE);
    assert(fabsf(f - 256 / SM64_SCALE) < 1);
    f = sm64_surface_find_floor_height(STL(-2) + mid, 500, mid);
    printf("floor in pit: %.1f (want 0)\n", f);
    assert(fabsf(f) < 1);

    struct SM64SurfaceCollisionData *ceil;
    float c = sm64_surface_find_ceil(mid, 200, mid, &ceil);
    printf("ceiling: %.1f (want %.1f)\n", c, SM64_CEILING_HEIGHT / SM64_SCALE);
    assert(ceil && fabsf(c - SM64_CEILING_HEIGHT / SM64_SCALE) < 1);

    // Walk east into the wall at x=12: should be pushed back west (x decreases)
    float x = STL(2) - 10, y = 256 / SM64_SCALE + 30, z = mid;
    int hits = sm64_surface_find_wall_collision(&x, &y, &z, 0, 50);
    printf("wall east: hits=%d pushed x %.1f -> %.1f\n", hits, STL(2) - 10, x);
    assert(hits > 0 && x < STL(2) - 10);

    // Standing in the pit next to its east edge: wall should push back west
    x = STL(-1) - 10; y = 30; z = mid;
    hits = sm64_surface_find_wall_collision(&x, &y, &z, 0, 50);
    printf("pit east edge: hits=%d pushed x %.1f -> %.1f\n", hits, STL(-1) - 10, x);
    assert(hits > 0 && x < STL(-1) - 10);

    // Mid-floor nowhere near a wall: no hit
    x = mid; y = 256 / SM64_SCALE + 30; z = mid;
    assert(sm64_surface_find_wall_collision(&x, &y, &z, 0, 50) == 0);
    puts("collision OK");
    return 0;
}
