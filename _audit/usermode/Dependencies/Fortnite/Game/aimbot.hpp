#include <cmath>
#include <chrono>
#include <thread>
#include <windows.h>
#include "../../Fortnite/Game/UE_Functions\entity_utils.hpp"
#include "../../../Includes/Definitions/xorst.h"
#include "../../../Includes/Definitions/c_obfuscation.hpp"
#include <codecvt>
#include <locale>
#include <string>
#include <mutex>
#include "../../../Includes/Mouse.h"

std::mutex PlayerMutex_;

// ============================================================================
//  Aim Method: Kernel Driver MouseClassServiceCallback
//
//  All mouse movement goes through device_().MoveMouse() which sends an IOCTL
//  to the kernel driver. The driver calls MouseClassServiceCallback directly
//  with the correct EAC ExtraInformation value set on each input packet.
// ============================================================================

// ---- Smoothing types ----
enum SmoothType : int {
    SMOOTH_LINEAR = 0,
    SMOOTH_BEZIER = 1,
    SMOOTH_HUMANIZED = 2
};

// ---- Target stick state ----
static uintptr_t g_StickTarget = 0;
static std::chrono::steady_clock::time_point g_StickStartTime;
static bool g_HasStickTarget = false;

// ---- Aim shake state ----
static std::mt19937 g_AimRng(std::random_device{}());

double get_cross_distance1(double x_, double y_)
{
    return sqrtf(powf((c_screen_width - x_), 2) + powf((c_screen_height - y_), 2));
}

float RandomFloat(float a, float b)
{
    std::uniform_real_distribution<float> dist(a, b);
    return dist(g_AimRng);
}

// ---- Bezier curve interpolation for smooth aim ----
// Attempt a natural-looking, non-linear path from current crosshair to target
static fvector2d BezierPoint(fvector2d p0, fvector2d p1, fvector2d p2, float t)
{
    float u = 1.0f - t;
    return fvector2d(
        u * u * p0.x + 2 * u * t * p1.x + t * t * p2.x,
        u * u * p0.y + 2 * u * t * p1.y + t * t * p2.y
    );
}

static fvector2d ComputeBezierDelta(float dx, float dy, float smoothFactor)
{
    // Start point is screen center (0,0 in delta space)
    // End point is the target delta (dx, dy)
    // Control point adds a slight curve to make movement non-linear
    fvector2d p0(0.0f, 0.0f);
    fvector2d p2(dx, dy);

    // Offset the control point perpendicular to the aim direction
    float perpX = -dy * 0.15f;
    float perpY = dx * 0.15f;
    fvector2d p1(dx * 0.5f + perpX, dy * 0.5f + perpY);

    // t represents how far along the curve we move per frame
    // Higher smooth = smaller t = slower movement
    float t = 1.0f / (smoothFactor * 0.8f);
    if (t > 1.0f) t = 1.0f;
    if (t < 0.02f) t = 0.02f;

    fvector2d point = BezierPoint(p0, p1, p2, t);
    return point;
}

// ---- Humanized aim: add micro-jitter and slight randomness to movement ----
static fvector2d HumanizeMovement(float dx, float dy, float smoothFactor)
{
    // Base movement with smooth factor
    float moveX = dx / smoothFactor;
    float moveY = dy / smoothFactor;

    // Add micro-jitter scaled to distance (closer = less jitter)
    float distance = sqrtf(dx * dx + dy * dy);
    float jitterScale = fminf(distance * 0.008f, 1.5f);

    float jitterX = RandomFloat(-jitterScale, jitterScale);
    float jitterY = RandomFloat(-jitterScale, jitterScale);

    moveX += jitterX;
    moveY += jitterY;

    // Slight speed variation (95%-105% of intended speed)
    float speedVariation = RandomFloat(0.95f, 1.05f);
    moveX *= speedVariation;
    moveY *= speedVariation;

    return fvector2d(moveX, moveY);
}

// ---- Aim shake: add constant micro-movement to simulate hand tremor ----
static fvector2d ApplyAimShake(fvector2d move, float shakeIntensity)
{
    if (shakeIntensity <= 0.0f) return move;

    float shakeX = RandomFloat(-shakeIntensity, shakeIntensity);
    float shakeY = RandomFloat(-shakeIntensity, shakeIntensity);

    return fvector2d(move.x + shakeX, move.y + shakeY);
}

// ============================================================================
//  No Recoil: Zero out weapon recoil vectors each frame
//  Writes 0.0f to vertical, horizontal, and recovery recoil offsets
// ============================================================================
void apply_no_recoil() {
    // Control-flow flattened to defeat decompiler analysis
    uintptr_t pawn = 0, weapon = 0;
    CFG_FLATTEN_BEGIN(norecoil)
        CFG_STATE(norecoil, 0) {
            auto& cache = GameCache::Cache::getInstance();
            pawn = cache.get_local_pawn().value_or(0);
            if (!pawn) { CFG_NEXT(norecoil, 99) }
            else { CFG_NEXT(norecoil, 1) }
        }
        CFG_STATE(norecoil, 1) {
            weapon = device_().read<uintptr_t>(pawn + offset_()->get_apply_offsets().CurrentWeapon());
            if (!weapon) { CFG_NEXT(norecoil, 99) }
            else { CFG_NEXT(norecoil, 2) }
        }
        CFG_STATE(norecoil, 2) {
            device_().write<double>(weapon + weaponoffsets::RecoilVert, 0.0);
            device_().write<double>(weapon + weaponoffsets::RecoilHoriz, 0.0);
            device_().write<double>(weapon + weaponoffsets::RecoilRecovery, 0.0);
            CFG_NEXT(norecoil, 99)
        }
    CFG_FLATTEN_END(norecoil)
}

// ============================================================================
//  No Spread: Decrypt and zero the XOR-encrypted spread fields
//  Pad bytes at weapon+0x23BA/0x23BB encode the spread seed.
//  XOR key: ((pad - 0xE886) & 0x82FFFFFE) | 0x4C000001
//  Writing the neutralizing value forces spread to zero.
// ============================================================================
void apply_no_spread() {
    // Control-flow flattened: XOR spread neutralization
    uintptr_t pawn = 0, weapon = 0;
    uint8_t padA = 0, padB = 0;
    uint32_t xor_key = 0;

    CFG_FLATTEN_BEGIN(nospread)
        CFG_STATE(nospread, 0) {
            auto& cache = GameCache::Cache::getInstance();
            pawn = cache.get_local_pawn().value_or(0);
            if (!pawn) { CFG_NEXT(nospread, 99) }
            else { CFG_NEXT(nospread, 1) }
        }
        CFG_STATE(nospread, 1) {
            weapon = device_().read<uintptr_t>(pawn + offset_()->get_apply_offsets().CurrentWeapon());
            if (!weapon) { CFG_NEXT(nospread, 99) }
            else { CFG_NEXT(nospread, 2) }
        }
        CFG_STATE(nospread, 2) {
            // Read pad bytes that encode spread seed
            padA = device_().read<uint8_t>(weapon + weaponoffsets::SpreadPadA);
            padB = device_().read<uint8_t>(weapon + weaponoffsets::SpreadPadB);
            CFG_NEXT(nospread, 3)
        }
        CFG_STATE(nospread, 3) {
            // Compute XOR key from pad values (Popstar method)
            uint32_t combined = (padB << 8) | padA;
            xor_key = ((combined - 0xE886) & 0x82FFFFFE) | 0x4C000001;
            CFG_NEXT(nospread, 4)
        }
        CFG_STATE(nospread, 4) {
            // 43.00: encrypted spread pad offsets are NOT published in current dumps.
            // Old write target (0x23B8) now sits past WeaponRecoilOffset (0x23A8 FRotator)
            // and is an unverified field - writing there risks corrupting weapon data.
            // Disabled until verified 43.00 spread offsets surface.
            (void)xor_key;
            CFG_NEXT(nospread, 99)
        }
    CFG_FLATTEN_END(nospread)
}

// ============================================================================
//  Smart Target Scoring: Weighted multi-factor target selection
//  80% crosshair proximity + 15% world distance + 5% visibility bonus
//  Replaces simple "closest to crosshair" with intelligent scoring
// ============================================================================
struct TargetScore {
    uintptr_t entity;
    float score; // lower = better
    bool visible;
};

float compute_target_score(float crosshair_dist, float world_dist, bool is_vis) {
    // Normalize crosshair distance (0-1 range, assuming max 500px)
    float norm_crosshair = fminf(crosshair_dist / 500.0f, 1.0f);
    // Normalize world distance (0-1 range, assuming max 300m)
    float norm_world = fminf(world_dist / 300.0f, 1.0f);
    // Visibility bonus (0 if visible, 0.5 penalty if not)
    float vis_penalty = is_vis ? 0.0f : 0.5f;

    // Weighted score - lower is better target
    return (norm_crosshair * 0.80f) + (norm_world * 0.15f) + (vis_penalty * 0.05f);
}

// ============================================================================
//  Bone Oscillation (Linear Aim): Smoothstep between two bones over time
//  Creates a natural-looking aim pattern that shifts between head and chest
//  Uses smoothstep interpolation for organic movement
// ============================================================================
static float g_OscillationTimer = 0.0f;
static auto g_OscillationLastTime = std::chrono::steady_clock::now();

float smoothstep(float edge0, float edge1, float x) {
    float t = fminf(fmaxf((x - edge0) / (edge1 - edge0), 0.0f), 1.0f);
    return t * t * (3.0f - 2.0f * t);
}

fvector get_oscillating_bone(uintptr_t mesh, float speed) {
    auto now = std::chrono::steady_clock::now();
    float dt = std::chrono::duration<float>(now - g_OscillationLastTime).count();
    g_OscillationLastTime = now;
    g_OscillationTimer += dt * speed;

    // Oscillate between head(110) and chest(66)
    float t = (sinf(g_OscillationTimer) + 1.0f) * 0.5f; // 0 to 1
    t = smoothstep(0.0f, 1.0f, t); // smooth the transition

    fvector head = bone_manager.get_bone(110);
    fvector chest = bone_manager.get_bone(66);

    return fvector(
        head.x + (chest.x - head.x) * t,
        head.y + (chest.y - head.y) * t,
        head.z + (chest.z - head.z) * t
    );
}

// ---- Driver mouse movement (routed through volatile to hide call target) ----
void move_mouse(int x, int y) {
    volatile auto fn = &m_vm::MoveMouse;
    (device_().*fn)(x, y, 0);
}

// ---- Driver mouse click ----
void mouse_click() {
    Mouse::LeftDown();
    Sleep(15 + (g_AimRng() % 20)); // Humanized click delay
    Mouse::LeftUp();
}

// Sub-pixel remainder so small movements accumulate instead of being lost to int truncation
static float g_RemainderX = 0.0f;
static float g_RemainderY = 0.0f;

// Frame-time reference for framerate-independent smoothing (60fps baseline)
static std::chrono::steady_clock::time_point g_LastAimTime = std::chrono::steady_clock::now();

void perform(fvector2d Head2D)
{
    float ScreenCenterX = c_screen_width / 2.0f;
    float ScreenCenterY = c_screen_height / 2.0f;

    // Framerate-independent smoothing: at 60fps the divisor is exactly c_smooth.
    // Faster frames shrink per-call movement proportionally, so the crosshair
    // covers the same screen distance per second at 240fps as at 60fps.
    auto now = std::chrono::steady_clock::now();
    float dt = std::chrono::duration<float>(now - g_LastAimTime).count();
    g_LastAimTime = now;
    if (dt < 0.0005f) dt = 0.0005f;   // guard div-by-zero on back-to-back calls
    if (dt > 0.1f)    dt = 0.1f;      // clamp stalls (tab-out/hitch) -> catch-up snap
    float AimSpeed = c_smooth / (dt * 60.0f);
    if (AimSpeed < 1.0f) AimSpeed = 1.0f; // never invert smoothing

    float dx = Head2D.x - ScreenCenterX;
    float dy = Head2D.y - ScreenCenterY;

    fvector2d Target(0, 0);

    switch (C_Aimbot::c_aim1) {
    case SMOOTH_LINEAR:
        Target.x = dx / AimSpeed;
        Target.y = dy / AimSpeed;
        break;
    case SMOOTH_BEZIER:
        Target = ComputeBezierDelta(dx, dy, AimSpeed);
        Target = ApplyAimShake(Target, C_Aimbot::c_aim_shake);
        break;
    case SMOOTH_HUMANIZED:
        Target = HumanizeMovement(dx, dy, AimSpeed);
        Target = ApplyAimShake(Target, C_Aimbot::c_aim_shake);
        break;
    }

    if (C_Aimbot::c_dead_zone) {
        if (fabsf(Target.x) <= C_Aimbot::c_custom_dead_zone && fabsf(Target.y) <= C_Aimbot::c_custom_dead_zone) {
            return;
        }
    }

    // Accumulate sub-pixel remainders so small deltas don't get lost
    Target.x += g_RemainderX;
    Target.y += g_RemainderY;

    int moveX = (int)Target.x;
    int moveY = (int)Target.y;

    g_RemainderX = Target.x - (float)moveX;
    g_RemainderY = Target.y - (float)moveY;

    if (moveX != 0 || moveY != 0) {
        move_mouse(moveX, moveY);
    }
}

// ---- Target stick logic ----
// Returns true if we should keep aiming at the stuck target
bool ShouldStickToTarget(uintptr_t candidateTarget) {
    if (!C_Aimbot::c_target_stick) {
        g_HasStickTarget = false;
        return false;
    }

    // If we have a stuck target, keep it
    if (g_HasStickTarget && g_StickTarget != 0) {
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - g_StickStartTime).count();

        // Release stick after timeout (prevent permanent lock)
        if (elapsed > C_Aimbot::c_stick_timeout_ms) {
            g_HasStickTarget = false;
            g_StickTarget = 0;
            return false;
        }
        return true;
    }

    // Lock onto new target
    if (candidateTarget != 0) {
        g_StickTarget = candidateTarget;
        g_StickStartTime = std::chrono::steady_clock::now();
        g_HasStickTarget = true;
        return true;
    }

    return false;
}

uintptr_t GetStickTarget() {
    return g_StickTarget;
}

void ResetStickTarget() {
    g_HasStickTarget = false;
    g_StickTarget = 0;
}
