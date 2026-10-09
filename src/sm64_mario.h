/******************************************************************************/
// Free implementation of Bullfrog's Dungeon Keeper strategy game.
/******************************************************************************/
/** @file sm64_mario.h
 *     Header file for sm64_mario.c.
 * @par Purpose:
 *     Drive a possessed creature with Super Mario 64 physics via libsm64.
 * @par Comment:
 *     Single player only. Needs sm64.dll and a user-supplied sm64.us.z64
 *     in the game directory; nothing is loaded until the command is used.
 */
/******************************************************************************/
#ifndef DK_SM64_MARIO_H
#define DK_SM64_MARIO_H

#include "bflib_basics.h"
#include "globals.h"

#ifdef __cplusplus
extern "C" {
#endif
/******************************************************************************/
struct Thing;

/** Returns NULL on success, or a message saying why Mario could not start. */
const char *sm64_mario_start(struct Thing *thing);
void sm64_mario_stop(void);
TbBool sm64_mario_controls(const struct Thing *thing);
void sm64_mario_set_input(uint32_t control_flags);
void sm64_mario_update(struct Thing *thing);
/** Angle to draw the creature at: Mario's facing when he controls it, else its own. */
short sm64_mario_display_angle(const struct Thing *thing);
/** How far behind the creature the chase camera sits, given the raised eye height. */
int sm64_mario_camera_distance(const struct Thing *thing, int eye_height);
struct Camera;
/** Lakitu: while Mario runs, eases the local camera's yaw round behind him. */
void sm64_mario_follow_camera(struct Camera *cam);
// ponytail: fixed Super Mario 64 style chase framing; tune by eye, or tie to the zoom keys if wanted
#define SM64_CHASE_RAISE 250
#define SM64_CHASE_TILT (-128) // camera pitch added for drawing only; negative looks down (about 22 degrees)
/******************************************************************************/
#ifdef __cplusplus
}
#endif
#endif
