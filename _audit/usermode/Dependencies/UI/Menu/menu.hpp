#pragma once
#include "../../../Includes/includes.hpp"
#include "../Fonts/Extras/colors.h"
#include "../Drawing/color_widgets.h"
#include "../Drawing/imgui_internal.h"
#include "../../../Includes/Definitions/c_definitions.hpp"
#include <d3d11.h>
#include <wrl/client.h>
#include <wincodec.h>
#include "../../D3D/d3dx9.h"
#include <d3d11.h>
#include <wrl/client.h>
#include <vector>
#include <fstream>
#include <filesystem>
#include <string>
//#include "../Renderer/c_overlay.h"
using namespace ImGui;
using namespace C_Players;
using namespace C_Aimbot;

inline BOOL WritePrivateProfileInt(LPCSTR lpAppName, LPCSTR lpKeyName, int nInteger, LPCSTR lpFileName) {
    char lpString[1024];
    sprintf_s(lpString, sizeof(lpString), TEXT("%d"), nInteger);
    return WritePrivateProfileStringA(lpAppName, lpKeyName, lpString, lpFileName);
}

inline BOOL WritePrivateProfileFloat(LPCSTR lpAppName, LPCSTR lpKeyName, float nInteger, LPCSTR lpFileName) {
    char lpString[1024];
    sprintf_s(lpString, sizeof(lpString), TEXT("%f"), nInteger);
    return WritePrivateProfileStringA(lpAppName, lpKeyName, lpString, lpFileName);
}

inline float GetPrivateProfileFloat(LPCSTR lpAppName, LPCSTR lpKeyName, FLOAT flDefault, LPCSTR lpFileName)
{
    char szData[32];
    GetPrivateProfileStringA(lpAppName, lpKeyName, std::to_string(flDefault).c_str(), szData, 32, lpFileName);
    return (float)atof(szData);
}

inline static int MyGetPrivateProfileInt(LPCSTR lpAppName, LPCSTR lpKeyName, FLOAT flDefault, LPCSTR lpFileName)
{
    char szData[32];
    GetPrivateProfileStringA(lpAppName, lpKeyName, std::to_string(flDefault).c_str(), szData, 32, lpFileName);
    return (int)atof(szData);
}

inline static void savecfg(LPCSTR path)
{
    WritePrivateProfileInt(("a"), TEXT("Aimbot"), c_enable, path);
    WritePrivateProfileInt(("a"), TEXT("Gun Configs"), rGunConfigs, path);
    WritePrivateProfileInt(("a"), TEXT("Triggerbot"), c_triggggger, path);
    WritePrivateProfileInt(("a"), TEXT("Prediction"), c_prediction, path);
    WritePrivateProfileInt(("a"), TEXT("DeadZone"), c_dead_zone, path);
    WritePrivateProfileInt(("a"), TEXT("Visible Check"), c_visible_check, path);
    WritePrivateProfileInt(("a"), TEXT("FOV Circle"), c_fov, path);
    WritePrivateProfileInt(("a"), TEXT("Aimline"), c_target, path);
    WritePrivateProfileInt(("a"), TEXT("Ignore Downed"), c_ignore_downed, path);
    WritePrivateProfileInt(("a"), TEXT("Ignore Bots"), c_ignore_bots, path);
    WritePrivateProfileInt(("a"), TEXT("Aimkey"), c_aim_key, path);
    WritePrivateProfileInt(("a"), TEXT("Trigkey"), c_trig_key, path);
    WritePrivateProfileFloat(("a"), TEXT("Triggerbot Delay"), trig_delay, path);
    WritePrivateProfileInt(("a"), TEXT("Humanization"), c_humanized, path);
    WritePrivateProfileFloat(("a"), TEXT("Smoothing0"), c_smooth, path);
    WritePrivateProfileFloat(("a"), TEXT("Smoothing1"), c_smooth, path);
    WritePrivateProfileFloat(("a"), TEXT("Smoothing2"), c_smooth, path);
    WritePrivateProfileFloat(("a"), TEXT("Smoothing3"), c_smooth, path);
    WritePrivateProfileFloat(("a"), TEXT("Smoothing4"), c_smooth, path);
    WritePrivateProfileFloat(("a"), TEXT("Smoothing5"), c_smooth, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov0"), c_fov, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov1"), c_fov, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov2"), c_fov, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov3"), c_fov, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov4"), c_fov, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov5"), c_fov, path);
    WritePrivateProfileFloat(("a"), TEXT("FOV Color1"), FovColor.x, path);
    WritePrivateProfileFloat(("a"), TEXT("FOV Color2"), FovColor.y, path);
    WritePrivateProfileFloat(("a"), TEXT("FOV Color3"), FovColor.z, path);
    WritePrivateProfileFloat(("a"), TEXT("FOV Color4"), FovColor.w, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov Fill Color1"), FovFillColor.x, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov Fill Color2"), FovFillColor.y, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov Fill Color3"), FovFillColor.z, path);
    WritePrivateProfileFloat(("a"), TEXT("Fov Fill Color4"), FovFillColor.w, path);
    WritePrivateProfileFloat(("a"), TEXT("Fill Color1"), FillColorVis.x, path);
    WritePrivateProfileFloat(("a"), TEXT("Fill Color2"), FillColorVis.y, path);
    WritePrivateProfileFloat(("a"), TEXT("Fill Color3"), FillColorVis.z, path);
    WritePrivateProfileFloat(("a"), TEXT("Fill Color4"), FillColorVis.w, path);
    WritePrivateProfileFloat(("a"), TEXT("Fill ColorHid1"), FillColorHidden.x, path);
    WritePrivateProfileFloat(("a"), TEXT("Fill ColorHid2"), FillColorHidden.y, path);
    WritePrivateProfileFloat(("a"), TEXT("Fill ColorHid3"), FillColorHidden.z, path);
    WritePrivateProfileFloat(("a"), TEXT("Fill ColorHid4"), FillColorHidden.w, path);
    WritePrivateProfileFloat(("a"), TEXT("Aimline Color1"), AimlineColor.x, path);
    WritePrivateProfileFloat(("a"), TEXT("Aimline Color2"), AimlineColor.y, path);
    WritePrivateProfileFloat(("a"), TEXT("Aimline Color3"), AimlineColor.z, path);
    WritePrivateProfileFloat(("a"), TEXT("Aimline Color4"), AimlineColor.w, path);
    WritePrivateProfileInt(("b"), TEXT("Box Type"), c_box, path);
    WritePrivateProfileInt(("b"), TEXT("Skeleton"), c_skeleton, path);
    WritePrivateProfileInt(("b"), TEXT("Chams"), c_charms, path);
    WritePrivateProfileInt(("b"), TEXT("Wireframe"), c_wireframe, path);
    WritePrivateProfileInt(("b"), TEXT("Distance"), c_distance, path);
    WritePrivateProfileInt(("b"), TEXT("FPS"), C_Overlay::c_enable_vsync, path);
    WritePrivateProfileFloat(("a"), TEXT("Accent Color1"), AccentColor.x, path);
    WritePrivateProfileFloat(("a"), TEXT("Accent Color2"), AccentColor.y, path);
    WritePrivateProfileFloat(("a"), TEXT("Accent Color3"), AccentColor.z, path);
    WritePrivateProfileFloat(("a"), TEXT("Accent Color4"), AccentColor.w, path);
    WritePrivateProfileFloat(("b"), TEXT("Box Color1"), BoxColorVis.x, path);
    WritePrivateProfileFloat(("b"), TEXT("Box Color2"), BoxColorVis.y, path);
    WritePrivateProfileFloat(("b"), TEXT("Box Color3"), BoxColorVis.z, path);
    WritePrivateProfileFloat(("b"), TEXT("Box Color4"), BoxColorVis.w, path);
    WritePrivateProfileFloat(("b"), TEXT("Skeleton Color1"), SkeletonColorVis.x, path);
    WritePrivateProfileFloat(("b"), TEXT("Skeleton Color2"), SkeletonColorVis.y, path);
    WritePrivateProfileFloat(("b"), TEXT("Skeleton Color3"), SkeletonColorVis.z, path);
    WritePrivateProfileFloat(("b"), TEXT("Skeleton Color4"), SkeletonColorVis.w, path);
    WritePrivateProfileFloat(("b"), TEXT("Distance Color1"), DistanceColorVis.x, path);
    WritePrivateProfileFloat(("b"), TEXT("Distance Color2"), DistanceColorVis.y, path);
    WritePrivateProfileFloat(("b"), TEXT("Distance Color3"), DistanceColorVis.z, path);
    WritePrivateProfileFloat(("b"), TEXT("Distance Color4"), DistanceColorVis.w, path);
    WritePrivateProfileFloat(("b"), TEXT("Username Color1"), UsernameColorVis.x, path);
    WritePrivateProfileFloat(("b"), TEXT("Username Color2"), UsernameColorVis.y, path);
    WritePrivateProfileFloat(("b"), TEXT("Username Color3"), UsernameColorVis.z, path);
    WritePrivateProfileFloat(("b"), TEXT("Username Color4"), UsernameColorVis.w, path);
    WritePrivateProfileFloat(("b"), TEXT("Kills Color1"), KillsColorVis.x, path);
    WritePrivateProfileFloat(("b"), TEXT("Kills Color2"), KillsColorVis.y, path);
    WritePrivateProfileFloat(("b"), TEXT("Kills Color3"), KillsColorVis.z, path);
    WritePrivateProfileFloat(("b"), TEXT("Kills Color4"), KillsColorVis.w, path);
    WritePrivateProfileFloat(("b"), TEXT("Weapon Color1"), WeaponColorVis.x, path);
    WritePrivateProfileFloat(("b"), TEXT("Weapon Color2"), WeaponColorVis.y, path);
    WritePrivateProfileFloat(("b"), TEXT("Weapon Color3"), WeaponColorVis.z, path);
    WritePrivateProfileFloat(("b"), TEXT("Weapon Color4"), WeaponColorVis.w, path);
    WritePrivateProfileFloat(("b"), TEXT("Snapline Color1"), SnaplineColorVis.x, path);
    WritePrivateProfileFloat(("b"), TEXT("Snapline Color2"), SnaplineColorVis.y, path);
    WritePrivateProfileFloat(("b"), TEXT("Snapline Color3"), SnaplineColorVis.z, path);
    WritePrivateProfileFloat(("b"), TEXT("Snapline Color4"), SnaplineColorVis.w, path);
    WritePrivateProfileFloat(("b"), TEXT("FOVArrow Color1"), FovArrowsColorVis.x, path);
    WritePrivateProfileFloat(("b"), TEXT("FOVArrow Color2"), FovArrowsColorVis.y, path);
    WritePrivateProfileFloat(("b"), TEXT("FOVArrow Color3"), FovArrowsColorVis.z, path);
    WritePrivateProfileFloat(("b"), TEXT("FOVArrow Color4"), FovArrowsColorVis.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidBox Color1"), BoxColorHidden.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidBox Color2"), BoxColorHidden.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidBox Color3"), BoxColorHidden.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidBox Color4"), BoxColorHidden.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color1"), SkeletonColorHidden.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color2"), SkeletonColorHidden.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color3"), SkeletonColorHidden.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color4"), SkeletonColorHidden.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidDistance Color1"), DistanceColorHidden.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidDistance Color2"), DistanceColorHidden.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidDistance Color3"), DistanceColorHidden.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidDistance Color4"), DistanceColorHidden.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidUsername Color1"), UsernameColorHidden.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidUsername Color2"), UsernameColorHidden.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidUsername Color3"), UsernameColorHidden.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidUsername Color4"), UsernameColorHidden.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color21"), c_color_visible.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color22"), c_color_visible.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color32"), c_color_visible.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color42"), c_color_visible.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color211"), c_color_invisible.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color221"), c_color_invisible.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color321"), c_color_invisible.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSkeleton Color241"), c_color_invisible.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidKills Color1"), KillsColorHidden.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidKills Color2"), KillsColorHidden.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidKills Color3"), KillsColorHidden.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidKills Color4"), KillsColorHidden.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidWeapon Color1"), WeaponColorHidden.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidWeapon Color2"), WeaponColorHidden.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidWeapon Color3"), WeaponColorHidden.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidWeapon Color4"), WeaponColorHidden.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSnapline Color1"), SnaplineColorHidden.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSnapline Color2"), SnaplineColorHidden.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSnapline Color3"), SnaplineColorHidden.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidSnapline Color4"), SnaplineColorHidden.w, path);
    WritePrivateProfileFloat(("b"), TEXT("HidFOVArrow Color1"), FovArrowsColorHidden.x, path);
    WritePrivateProfileFloat(("b"), TEXT("HidFOVArrow Color2"), FovArrowsColorHidden.y, path);
    WritePrivateProfileFloat(("b"), TEXT("HidFOVArrow Color3"), FovArrowsColorHidden.z, path);
    WritePrivateProfileFloat(("b"), TEXT("HidFOVArrow Color4"), FovArrowsColorHidden.w, path);

    WritePrivateProfileInt(("b"), TEXT("Render"), c_max_render_distance, path);
    WritePrivateProfileInt(("b"), TEXT("Head Circle"), c_headcircle, path);
    WritePrivateProfileInt(("b"), TEXT("Weapon Name"), c_weapon, path);
    WritePrivateProfileInt(("b"), TEXT("Platform Name"), c_platform, path);
    WritePrivateProfileInt(("b"), TEXT("Username"), c_username, path);
    WritePrivateProfileInt(("b"), TEXT("Rank"), c_rank_esp, path);
    WritePrivateProfileInt(("b"), TEXT("FOV Arrows"), c_arrows, path);
    WritePrivateProfileInt(("b"), TEXT("Snapline Position"), c_lines, path);
    WritePrivateProfileFloat(("b"), TEXT("ESP Distance"), c_distance, path);
    WritePrivateProfileInt(("b"), TEXT("Hitbox"), c_hitbox, path);
    WritePrivateProfileInt(("a"), TEXT("Risk"), c_threat, path);

    // Crosshair
    WritePrivateProfileInt(("c"), TEXT("CrosshairEnabled"), C_Crosshair::enabled, path);
    WritePrivateProfileFloat(("c"), TEXT("CrosshairRadius"), C_Crosshair::radius, path);
    WritePrivateProfileFloat(("c"), TEXT("CrosshairThickness"), C_Crosshair::thickness, path);
    WritePrivateProfileInt(("c"), TEXT("CrosshairSegments"), C_Crosshair::num_segments, path);
    WritePrivateProfileInt(("c"), TEXT("CrosshairOutline"), C_Crosshair::outline, path);
    WritePrivateProfileFloat(("c"), TEXT("CrosshairR"), C_Crosshair::color[0], path);
    WritePrivateProfileFloat(("c"), TEXT("CrosshairG"), C_Crosshair::color[1], path);
    WritePrivateProfileFloat(("c"), TEXT("CrosshairB"), C_Crosshair::color[2], path);
    WritePrivateProfileFloat(("c"), TEXT("CrosshairA"), C_Crosshair::color[3], path);

    // Movement ESP
    WritePrivateProfileInt(("c"), TEXT("MovementState"), C_Movement::show_movement, path);
    WritePrivateProfileInt(("c"), TEXT("MovementColor"), C_Movement::show_state_color, path);

    // Target Visuals
    WritePrivateProfileInt(("c"), TEXT("AimCurve"), C_TargetVisuals::aim_curve, path);
    WritePrivateProfileInt(("c"), TEXT("TargetDot"), C_TargetVisuals::target_dot, path);
    WritePrivateProfileInt(("c"), TEXT("TargetLine"), C_TargetVisuals::target_line, path);
    WritePrivateProfileInt(("c"), TEXT("DeadzoneDot"), C_TargetVisuals::deadzone_dot, path);
    WritePrivateProfileInt(("c"), TEXT("PredictionX"), C_TargetVisuals::prediction_x, path);
    WritePrivateProfileFloat(("c"), TEXT("DZRestriction"), C_TargetVisuals::restriction, path);

    // Game Info HUD
    WritePrivateProfileInt(("c"), TEXT("HudBar"), C_GameInfo::show_hud_bar, path);
    WritePrivateProfileInt(("c"), TEXT("PlayerCount"), C_GameInfo::show_player_count, path);
    WritePrivateProfileInt(("c"), TEXT("KillCount"), C_GameInfo::show_kill_count, path);
    WritePrivateProfileInt(("c"), TEXT("Spectators"), C_GameInfo::show_spectators, path);
}

inline static void loadcfg(LPCSTR path)
{
    c_color_invisible.x = GetPrivateProfileFloat(("a"), TEXT("HidSkeleton Color211"), c_color_invisible.x, path);
    c_color_invisible.y = GetPrivateProfileFloat(("a"), TEXT("HidSkeleton Color221"), c_color_invisible.y, path);
    c_color_invisible.z = GetPrivateProfileFloat(("a"), TEXT("HidSkeleton Color321"), c_color_invisible.z, path);
    c_color_invisible.w = GetPrivateProfileFloat(("a"), TEXT("HidSkeleton Color241"), c_color_invisible.w, path);
    c_threat = MyGetPrivateProfileInt(("a"), TEXT("Risk"), c_threat, path);
    c_enable = MyGetPrivateProfileInt(("a"), TEXT("Aimbot"), c_enable, path);
    c_hitbox = MyGetPrivateProfileInt(("a"), TEXT("Hitbox"), c_hitbox, path);
    rGunConfigs = MyGetPrivateProfileInt(("a"), TEXT("Gun Configs"), rGunConfigs, path);
    c_triggggger = MyGetPrivateProfileInt(("a"), TEXT("Triggerbot"), c_triggggger, path);
    c_prediction = MyGetPrivateProfileInt(("a"), TEXT("Prediction"), c_prediction, path);
    c_visible_check = MyGetPrivateProfileInt(("a"), TEXT("Visible Check"), c_visible_check, path);
    c_fov = MyGetPrivateProfileInt(("a"), TEXT("FOV Circle"), c_fov, path);
    c_target = MyGetPrivateProfileInt(("a"), TEXT("Aimline"), c_target, path);
    c_color_visible.x = GetPrivateProfileFloat(("a"), TEXT("HidSkeleton Color21"), c_color_visible.x, path);
    c_color_visible.y = GetPrivateProfileFloat(("a"), TEXT("HidSkeleton Color22"), c_color_visible.y, path);
    c_color_visible.z = GetPrivateProfileFloat(("a"), TEXT("HidSkeleton Color32"), c_color_visible.z, path);
    c_color_visible.w = GetPrivateProfileFloat(("a"), TEXT("HidSkeleton Color42"), c_color_visible.w, path);
    c_ignore_downed = MyGetPrivateProfileInt(("a"), TEXT("Ignore Downed"), c_ignore_downed, path);
    c_ignore_bots = MyGetPrivateProfileInt(("a"), TEXT("Ignore Bots"), c_ignore_bots, path);
    FovColor.x = GetPrivateProfileFloat(("a"), TEXT("FOV Color1"), FovColor.x, path);
    FovColor.y = GetPrivateProfileFloat(("a"), TEXT("FOV Color2"), FovColor.y, path);
    FovColor.z = GetPrivateProfileFloat(("a"), TEXT("FOV Color3"), FovColor.z, path);
    FovColor.w = GetPrivateProfileFloat(("a"), TEXT("FOV Color4"), FovColor.w, path);
    FovFillColor.x = GetPrivateProfileFloat(("a"), TEXT("Fov Fill Color1"), FovFillColor.x, path);
    FovFillColor.y = GetPrivateProfileFloat(("a"), TEXT("Fov Fill Color2"), FovFillColor.y, path);
    FovFillColor.z = GetPrivateProfileFloat(("a"), TEXT("Fov Fill Color3"), FovFillColor.z, path);
    FovFillColor.w = GetPrivateProfileFloat(("a"), TEXT("Fov Fill Color4"), FovFillColor.w, path);
    FillColorVis.x = GetPrivateProfileFloat(("a"), TEXT("Fill Color1"), FillColorVis.x, path);
    FillColorVis.y = GetPrivateProfileFloat(("a"), TEXT("Fill Color2"), FillColorVis.y, path);
    FillColorVis.z = GetPrivateProfileFloat(("a"), TEXT("Fill Color3"), FillColorVis.z, path);
    FillColorVis.w = GetPrivateProfileFloat(("a"), TEXT("Fill Color4"), FillColorVis.w, path);
    FillColorHidden.x = GetPrivateProfileFloat(("a"), TEXT("Fill ColorHid1"), FillColorHidden.x, path);
    FillColorHidden.y = GetPrivateProfileFloat(("a"), TEXT("Fill ColorHid2"), FillColorHidden.y, path);
    FillColorHidden.z = GetPrivateProfileFloat(("a"), TEXT("Fill ColorHid3"), FillColorHidden.z, path);
    FillColorHidden.w = GetPrivateProfileFloat(("a"), TEXT("Fill ColorHid4"), FillColorHidden.w, path);
    AimlineColor.x = GetPrivateProfileFloat(("a"), TEXT("Aimline Color1"), AimlineColor.x, path);
    AimlineColor.y = GetPrivateProfileFloat(("a"), TEXT("Aimline Color2"), AimlineColor.y, path);
    AimlineColor.z = GetPrivateProfileFloat(("a"), TEXT("Aimline Color3"), AimlineColor.z, path);
    AimlineColor.w = GetPrivateProfileFloat(("a"), TEXT("Aimline Color4"), AimlineColor.w, path);
    c_aim_key = MyGetPrivateProfileInt(("a"), TEXT("Aimkey"), c_aim_key, path);
    c_trig_key = MyGetPrivateProfileInt(("a"), TEXT("Trigkey"), c_trig_key, path);
    trig_delay = GetPrivateProfileFloat(("a"), TEXT("Triggerbot Delay"), trig_delay, path);
    c_humanized = GetPrivateProfileFloat(("a"), TEXT("Humanization"), c_humanized, path);
    c_smooth = GetPrivateProfileFloat(("a"), TEXT("Smoothing0"), c_smooth, path);
    c_smooth = GetPrivateProfileFloat(("a"), TEXT("Smoothing1"), c_smooth, path);
    c_smooth = GetPrivateProfileFloat(("a"), TEXT("Smoothing2"), c_smooth, path);
    c_smooth = GetPrivateProfileFloat(("a"), TEXT("Smoothing3"), c_smooth, path);
    c_smooth = GetPrivateProfileFloat(("a"), TEXT("Smoothing4"), c_smooth, path);
    c_smooth = GetPrivateProfileFloat(("a"), TEXT("Smoothing5"), c_smooth, path);
    c_fov = GetPrivateProfileFloat(("a"), TEXT("Fov0"), c_fov, path);
    c_fov = GetPrivateProfileFloat(("a"), TEXT("Fov1"), c_fov, path);
    c_fov = GetPrivateProfileFloat(("a"), TEXT("Fov2"), c_fov, path);
    c_fov = GetPrivateProfileFloat(("a"), TEXT("Fov3"), c_fov, path);
    c_fov = GetPrivateProfileFloat(("a"), TEXT("Fov4"), c_fov, path);
    c_fov = GetPrivateProfileFloat(("a"), TEXT("Fov5"), c_fov, path);
    c_dead_zone = MyGetPrivateProfileInt(("b"), TEXT("DeadZone"), c_dead_zone, path);
    c_max_render_distance = MyGetPrivateProfileInt(("b"), TEXT("Render"), c_max_render_distance, path);
    c_box = MyGetPrivateProfileInt(("b"), TEXT("Box Type"), c_box, path);
    c_skeleton = MyGetPrivateProfileInt(("b"), TEXT("Skeleton"), c_skeleton, path);
    c_charms = MyGetPrivateProfileInt(("b"), TEXT("Chams"), c_charms, path);
    c_wireframe = MyGetPrivateProfileInt(("b"), TEXT("Wireframe"), c_wireframe, path);
    c_distance = MyGetPrivateProfileInt(("b"), TEXT("Distance"), c_distance, path);
    C_Overlay::c_enable_vsync = MyGetPrivateProfileInt(("b"), TEXT("FPS"), C_Overlay::c_enable_vsync, path);
    AccentColor.x = GetPrivateProfileFloat(("a"), TEXT("Accent Color1"), AccentColor.x, path);
    AccentColor.y = GetPrivateProfileFloat(("a"), TEXT("Accent Color2"), AccentColor.y, path);
    AccentColor.z = GetPrivateProfileFloat(("a"), TEXT("Accent Color3"), AccentColor.z, path);
    AccentColor.w = GetPrivateProfileFloat(("a"), TEXT("Accent Color4"), AccentColor.w, path);
    BoxColorVis.x = GetPrivateProfileFloat(("b"), TEXT("Box Color1"), BoxColorVis.x, path);
    BoxColorVis.y = GetPrivateProfileFloat(("b"), TEXT("Box Color2"), BoxColorVis.y, path);
    BoxColorVis.z = GetPrivateProfileFloat(("b"), TEXT("Box Color3"), BoxColorVis.z, path);
    BoxColorVis.w = GetPrivateProfileFloat(("b"), TEXT("Box Color4"), BoxColorVis.w, path);
    SkeletonColorVis.x = GetPrivateProfileFloat(("b"), TEXT("Skeleton Color1"), SkeletonColorVis.x, path);
    SkeletonColorVis.y = GetPrivateProfileFloat(("b"), TEXT("Skeleton Color2"), SkeletonColorVis.y, path);
    SkeletonColorVis.z = GetPrivateProfileFloat(("b"), TEXT("Skeleton Color3"), SkeletonColorVis.z, path);
    SkeletonColorVis.w = GetPrivateProfileFloat(("b"), TEXT("Skeleton Color4"), SkeletonColorVis.w, path);
    DistanceColorVis.x = GetPrivateProfileFloat(("b"), TEXT("Distance Color1"), DistanceColorVis.x, path);
    DistanceColorVis.y = GetPrivateProfileFloat(("b"), TEXT("Distance Color2"), DistanceColorVis.y, path);
    DistanceColorVis.z = GetPrivateProfileFloat(("b"), TEXT("Distance Color3"), DistanceColorVis.z, path);
    DistanceColorVis.w = GetPrivateProfileFloat(("b"), TEXT("Distance Color4"), DistanceColorVis.w, path);
    UsernameColorVis.x = GetPrivateProfileFloat(("b"), TEXT("Username Color1"), UsernameColorVis.x, path);
    UsernameColorVis.y = GetPrivateProfileFloat(("b"), TEXT("Username Color2"), UsernameColorVis.y, path);
    UsernameColorVis.z = GetPrivateProfileFloat(("b"), TEXT("Username Color3"), UsernameColorVis.z, path);
    UsernameColorVis.w = GetPrivateProfileFloat(("b"), TEXT("Username Color4"), UsernameColorVis.w, path);
    KillsColorVis.x = GetPrivateProfileFloat(("b"), TEXT("Kills Color1"), KillsColorVis.x, path);
    KillsColorVis.y = GetPrivateProfileFloat(("b"), TEXT("Kills Color2"), KillsColorVis.y, path);
    KillsColorVis.z = GetPrivateProfileFloat(("b"), TEXT("Kills Color3"), KillsColorVis.z, path);
    KillsColorVis.w = GetPrivateProfileFloat(("b"), TEXT("Kills Color4"), KillsColorVis.w, path);
    WeaponColorVis.x = GetPrivateProfileFloat(("b"), TEXT("Weapon Color1"), WeaponColorVis.x, path);
    WeaponColorVis.y = GetPrivateProfileFloat(("b"), TEXT("Weapon Color2"), WeaponColorVis.y, path);
    WeaponColorVis.z = GetPrivateProfileFloat(("b"), TEXT("Weapon Color3"), WeaponColorVis.z, path);
    WeaponColorVis.w = GetPrivateProfileFloat(("b"), TEXT("Weapon Color4"), WeaponColorVis.w, path);
    SnaplineColorVis.x = GetPrivateProfileFloat(("b"), TEXT("Snapline Color1"), SnaplineColorVis.x, path);
    SnaplineColorVis.y = GetPrivateProfileFloat(("b"), TEXT("Snapline Color2"), SnaplineColorVis.y, path);
    SnaplineColorVis.z = GetPrivateProfileFloat(("b"), TEXT("Snapline Color3"), SnaplineColorVis.z, path);
    SnaplineColorVis.w = GetPrivateProfileFloat(("b"), TEXT("Snapline Color4"), SnaplineColorVis.w, path);
    FovArrowsColorVis.x = GetPrivateProfileFloat(("b"), TEXT("FOVArrow Color1"), FovArrowsColorVis.x, path);
    FovArrowsColorVis.y = GetPrivateProfileFloat(("b"), TEXT("FOVArrow Color2"), FovArrowsColorVis.y, path);
    FovArrowsColorVis.z = GetPrivateProfileFloat(("b"), TEXT("FOVArrow Color3"), FovArrowsColorVis.z, path);
    FovArrowsColorVis.w = GetPrivateProfileFloat(("b"), TEXT("FOVArrow Color4"), FovArrowsColorVis.w, path);
    DistanceColorHidden.x = GetPrivateProfileFloat(("b"), TEXT("HidDistance Color1"), DistanceColorHidden.x, path);
    DistanceColorHidden.y = GetPrivateProfileFloat(("b"), TEXT("HidDistance Color2"), DistanceColorHidden.y, path);
    DistanceColorHidden.z = GetPrivateProfileFloat(("b"), TEXT("HidDistance Color3"), DistanceColorHidden.z, path);
    DistanceColorHidden.w = GetPrivateProfileFloat(("b"), TEXT("HidDistance Color4"), DistanceColorHidden.w, path);
    UsernameColorHidden.x = GetPrivateProfileFloat(("b"), TEXT("HidUsername Color1"), UsernameColorHidden.x, path);
    UsernameColorHidden.y = GetPrivateProfileFloat(("b"), TEXT("HidUsername Color2"), UsernameColorHidden.y, path);
    UsernameColorHidden.z = GetPrivateProfileFloat(("b"), TEXT("HidUsername Color3"), UsernameColorHidden.z, path);
    UsernameColorHidden.w = GetPrivateProfileFloat(("b"), TEXT("HidUsername Color4"), UsernameColorHidden.w, path);
    KillsColorHidden.x = GetPrivateProfileFloat(("b"), TEXT("HidKills Color1"), KillsColorHidden.x, path);
    KillsColorHidden.y = GetPrivateProfileFloat(("b"), TEXT("HidKills Color2"), KillsColorHidden.y, path);
    KillsColorHidden.z = GetPrivateProfileFloat(("b"), TEXT("HidKills Color3"), KillsColorHidden.z, path);
    KillsColorHidden.w = GetPrivateProfileFloat(("b"), TEXT("HidKills Color4"), KillsColorHidden.w, path);
    WeaponColorHidden.x = GetPrivateProfileFloat(("b"), TEXT("HidWeapon Color1"), WeaponColorHidden.x, path);
    WeaponColorHidden.y = GetPrivateProfileFloat(("b"), TEXT("HidWeapon Color2"), WeaponColorHidden.y, path);
    WeaponColorHidden.z = GetPrivateProfileFloat(("b"), TEXT("HidWeapon Color3"), WeaponColorHidden.z, path);
    WeaponColorHidden.w = GetPrivateProfileFloat(("b"), TEXT("HidWeapon Color4"), WeaponColorHidden.w, path);
    SnaplineColorHidden.x = GetPrivateProfileFloat(("b"), TEXT("HidSnapline Color1"), SnaplineColorHidden.x, path);
    SnaplineColorHidden.y = GetPrivateProfileFloat(("b"), TEXT("HidSnapline Color2"), SnaplineColorHidden.y, path);
    SnaplineColorHidden.z = GetPrivateProfileFloat(("b"), TEXT("HidSnapline Color3"), SnaplineColorHidden.z, path);
    SnaplineColorHidden.w = GetPrivateProfileFloat(("b"), TEXT("HidSnapline Color4"), SnaplineColorHidden.w, path);
    FovArrowsColorHidden.x = GetPrivateProfileFloat(("b"), TEXT("HidFOVArrow Color1"), FovArrowsColorHidden.x, path);
    FovArrowsColorHidden.y = GetPrivateProfileFloat(("b"), TEXT("HidFOVArrow Color2"), FovArrowsColorHidden.y, path);
    FovArrowsColorHidden.z = GetPrivateProfileFloat(("b"), TEXT("HidFOVArrow Color3"), FovArrowsColorHidden.z, path);
    FovArrowsColorHidden.w = GetPrivateProfileFloat(("b"), TEXT("HidFOVArrow Color4"), FovArrowsColorHidden.w, path);
    BoxColorHidden.x = GetPrivateProfileFloat(("b"), TEXT("HidBox Color1"), BoxColorHidden.x, path);
    BoxColorHidden.y = GetPrivateProfileFloat(("b"), TEXT("HidBox Color2"), BoxColorHidden.y, path);
    BoxColorHidden.z = GetPrivateProfileFloat(("b"), TEXT("HidBox Color3"), BoxColorHidden.z, path);
    BoxColorHidden.w = GetPrivateProfileFloat(("b"), TEXT("HidBox Color4"), BoxColorHidden.w, path);
    c_hitbox = MyGetPrivateProfileInt(("b"), TEXT("Hitbox"), c_hitbox, path);
    c_headcircle = MyGetPrivateProfileInt(("b"), TEXT("Head Circle"), c_headcircle, path);
    c_weapon = MyGetPrivateProfileInt(("b"), TEXT("Weapon Name"), c_weapon, path);
    c_platform = MyGetPrivateProfileInt(("b"), TEXT("Platform Name"), c_platform, path);
    c_username = MyGetPrivateProfileInt(("b"), TEXT("Username"), c_username, path);
    c_rank_esp = MyGetPrivateProfileInt(("b"), TEXT("Rank"), c_rank_esp, path);
    c_arrows = MyGetPrivateProfileInt(("b"), TEXT("FOV Arrows"), c_arrows, path);
    c_lines = MyGetPrivateProfileInt(("b"), TEXT("Snapline Position"), c_lines, path);
    c_distance = GetPrivateProfileFloat(("b"), TEXT("ESP Distance"), c_distance, path);
    c_enable_box = GetPrivateProfileFloat(("b"), TEXT("c_enable_box"), c_enable_box, path);

    // Crosshair
    C_Crosshair::enabled = MyGetPrivateProfileInt(("c"), TEXT("CrosshairEnabled"), C_Crosshair::enabled, path);
    C_Crosshair::radius = GetPrivateProfileFloat(("c"), TEXT("CrosshairRadius"), C_Crosshair::radius, path);
    C_Crosshair::thickness = GetPrivateProfileFloat(("c"), TEXT("CrosshairThickness"), C_Crosshair::thickness, path);
    C_Crosshair::num_segments = MyGetPrivateProfileInt(("c"), TEXT("CrosshairSegments"), C_Crosshair::num_segments, path);
    C_Crosshair::outline = MyGetPrivateProfileInt(("c"), TEXT("CrosshairOutline"), C_Crosshair::outline, path);
    C_Crosshair::color[0] = GetPrivateProfileFloat(("c"), TEXT("CrosshairR"), C_Crosshair::color[0], path);
    C_Crosshair::color[1] = GetPrivateProfileFloat(("c"), TEXT("CrosshairG"), C_Crosshair::color[1], path);
    C_Crosshair::color[2] = GetPrivateProfileFloat(("c"), TEXT("CrosshairB"), C_Crosshair::color[2], path);
    C_Crosshair::color[3] = GetPrivateProfileFloat(("c"), TEXT("CrosshairA"), C_Crosshair::color[3], path);

    // Movement ESP
    C_Movement::show_movement = MyGetPrivateProfileInt(("c"), TEXT("MovementState"), C_Movement::show_movement, path);
    C_Movement::show_state_color = MyGetPrivateProfileInt(("c"), TEXT("MovementColor"), C_Movement::show_state_color, path);

    // Target Visuals
    C_TargetVisuals::aim_curve = MyGetPrivateProfileInt(("c"), TEXT("AimCurve"), C_TargetVisuals::aim_curve, path);
    C_TargetVisuals::target_dot = MyGetPrivateProfileInt(("c"), TEXT("TargetDot"), C_TargetVisuals::target_dot, path);
    C_TargetVisuals::target_line = MyGetPrivateProfileInt(("c"), TEXT("TargetLine"), C_TargetVisuals::target_line, path);
    C_TargetVisuals::deadzone_dot = MyGetPrivateProfileInt(("c"), TEXT("DeadzoneDot"), C_TargetVisuals::deadzone_dot, path);
    C_TargetVisuals::prediction_x = MyGetPrivateProfileInt(("c"), TEXT("PredictionX"), C_TargetVisuals::prediction_x, path);
    C_TargetVisuals::restriction = GetPrivateProfileFloat(("c"), TEXT("DZRestriction"), C_TargetVisuals::restriction, path);

    // Game Info HUD
    C_GameInfo::show_hud_bar = MyGetPrivateProfileInt(("c"), TEXT("HudBar"), C_GameInfo::show_hud_bar, path);
    C_GameInfo::show_player_count = MyGetPrivateProfileInt(("c"), TEXT("PlayerCount"), C_GameInfo::show_player_count, path);
    C_GameInfo::show_kill_count = MyGetPrivateProfileInt(("c"), TEXT("KillCount"), C_GameInfo::show_kill_count, path);
    C_GameInfo::show_spectators = MyGetPrivateProfileInt(("c"), TEXT("Spectators"), C_GameInfo::show_spectators, path);
}

inline void c_draw_fov()
{
    SPOOF_FUNC;
    if (c_circle) {
        GetBackgroundDrawList()->AddCircle(ImVec2(C_Overlay::c_screen_width / 2, C_Overlay::c_screen_height / 2), c_fov, ImColor(FovColor), 64, 1.2f);
    }
}

// ============================================================
//  ABLE UI — OBSIDIAN & DYNAMIC ACCENT ARCHITECTURE
// ============================================================

#define BG_DARKEST      IM_COL32(10, 10, 15, 255)       // main window canvas
#define BG_SIDEBAR      IM_COL32(13, 13, 19, 255)       // left navigation rail
#define BG_HEADER       IM_COL32(14, 14, 21, 255)       // top bar surface
#define BG_CHILD        IM_COL32(16, 16, 24, 255)       // card body surface
#define BG_CARD_TOP     IM_COL32(20, 20, 30, 255)       // card header band
#define BG_FRAME        IM_COL32(22, 22, 33, 255)       // widget frame
#define BG_FRAME_HOV    IM_COL32(29, 29, 43, 255)       // hovered widget frame
#define BG_FRAME_ACT    IM_COL32(35, 34, 52, 255)       // active widget frame
#define BORDER_COL      IM_COL32(28, 28, 42, 255)       // crisp 1px card border
#define BORDER_SUBTLE   IM_COL32(24, 24, 36, 255)       // inner divider line
#define TEXT_BRIGHT     IM_COL32(240, 238, 252, 255)    // primary label text
#define TEXT_MID        IM_COL32(168, 164, 190, 255)    // secondary text
#define TEXT_DIM        IM_COL32(104, 100, 126, 255)    // muted text

namespace idfk {
    inline bool bShowMenu = true;
    inline int m_MenuTab = 0;
    inline int m_WeaponSubTab = 0;
    inline int m_CombatVisSubTab = 0;
    inline int m_ColorSubTab = 0;
}

extern ImFont* ui_font;
extern ImFont* ui_bold_font;

inline ImU32 AccU32(float alpha = 1.0f) {
    ImVec4 v = MenuAccent.Value;
    return ImGui::ColorConvertFloat4ToU32(ImVec4(v.x, v.y, v.z, v.w * alpha));
}

inline ImU32 AccLightU32(float alpha = 1.0f) {
    ImVec4 v = MenuAccent.Value;
    return ImGui::ColorConvertFloat4ToU32(ImVec4(
        ImMin(1.0f, v.x + 0.16f),
        ImMin(1.0f, v.y + 0.16f),
        ImMin(1.0f, v.z + 0.16f),
        alpha));
}

inline static void apply_theme() {
    SPOOF_FUNC;
    colors::accent_color = MenuAccent.Value;

    auto& s = ImGui::GetStyle();

    s.WindowPadding     = ImVec2(10, 8);
    s.FramePadding      = ImVec2(10, 5);
    s.ItemSpacing       = ImVec2(8, 7);
    s.ItemInnerSpacing  = ImVec2(6, 4);
    s.IndentSpacing     = 16.0f;

    s.WindowBorderSize  = 1.0f;
    s.ChildBorderSize   = 1.0f;
    s.FrameBorderSize   = 0.0f;
    s.PopupBorderSize   = 1.0f;

    s.WindowRounding    = 10.0f;
    s.ChildRounding     = 8.0f;
    s.FrameRounding     = 5.0f;
    s.PopupRounding     = 7.0f;
    s.ScrollbarRounding = 8.0f;
    s.GrabRounding      = 5.0f;
    s.TabRounding       = 5.0f;

    s.ScrollbarSize     = 6.0f;
    s.GrabMinSize       = 8.0f;

    ImVec4 acc = MenuAccent.Value;
    ImVec4 acc_dim = ImVec4(acc.x * 0.65f, acc.y * 0.65f, acc.z * 0.65f, 1.0f);
    ImVec4 acc_light = ImVec4(ImMin(1.f, acc.x + 0.15f), ImMin(1.f, acc.y + 0.15f), ImMin(1.f, acc.z + 0.15f), 1.0f);

    auto* c = s.Colors;
    c[ImGuiCol_WindowBg]             = ImColor(BG_DARKEST);
    c[ImGuiCol_ChildBg]              = ImColor(BG_CHILD);
    c[ImGuiCol_PopupBg]              = ImColor(16, 16, 24, 250);
    c[ImGuiCol_Text]                 = ImColor(TEXT_BRIGHT);
    c[ImGuiCol_TextDisabled]         = ImColor(TEXT_DIM);
    c[ImGuiCol_Border]               = ImColor(BORDER_COL);
    c[ImGuiCol_BorderShadow]         = ImColor(0, 0, 0, 0);
    c[ImGuiCol_FrameBg]              = ImColor(BG_FRAME);
    c[ImGuiCol_FrameBgHovered]       = ImColor(BG_FRAME_HOV);
    c[ImGuiCol_FrameBgActive]        = ImColor(BG_FRAME_ACT);
    c[ImGuiCol_TitleBg]              = ImColor(BG_DARKEST);
    c[ImGuiCol_TitleBgActive]        = ImColor(BG_DARKEST);
    c[ImGuiCol_TitleBgCollapsed]     = ImColor(BG_DARKEST);
    c[ImGuiCol_ScrollbarBg]          = ImColor(12, 12, 18, 120);
    c[ImGuiCol_ScrollbarGrab]        = ImColor(42, 40, 60);
    c[ImGuiCol_ScrollbarGrabHovered] = ImColor(60, 56, 86);
    c[ImGuiCol_ScrollbarGrabActive]  = acc;
    c[ImGuiCol_Button]               = ImColor(BG_FRAME);
    c[ImGuiCol_ButtonHovered]        = ImColor(BG_FRAME_HOV);
    c[ImGuiCol_ButtonActive]         = acc_dim;
    c[ImGuiCol_CheckMark]            = acc;
    c[ImGuiCol_SliderGrab]           = acc;
    c[ImGuiCol_SliderGrabActive]     = acc_light;
    c[ImGuiCol_Header]               = ImVec4(acc.x, acc.y, acc.z, 0.22f);
    c[ImGuiCol_HeaderHovered]        = ImVec4(acc.x, acc.y, acc.z, 0.35f);
    c[ImGuiCol_HeaderActive]         = ImVec4(acc.x, acc.y, acc.z, 0.48f);
    c[ImGuiCol_Separator]            = ImColor(BORDER_SUBTLE);
    c[ImGuiCol_SeparatorHovered]     = acc;
    c[ImGuiCol_SeparatorActive]      = acc;
    c[ImGuiCol_ResizeGrip]           = ImColor(0, 0, 0, 0);
    c[ImGuiCol_ResizeGripHovered]    = ImColor(0, 0, 0, 0);
    c[ImGuiCol_ResizeGripActive]     = ImColor(0, 0, 0, 0);
    c[ImGuiCol_Tab]                  = ImColor(BG_CHILD);
    c[ImGuiCol_TabHovered]           = ImColor(38, 34, 52);
    c[ImGuiCol_TabActive]            = ImColor(48, 42, 66);
}

// ---- Able UI Component Helpers ----
inline void BeginCard(const char* id, const char* title, const char* subtitle, ImVec2 size) {
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0.0f, 0.0f));
    ImGui::BeginChild(id, size, true, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    ImGui::PopStyleVar();

    auto* dl = ImGui::GetWindowDrawList();
    ImVec2 wp = ImGui::GetWindowPos();
    float ww = ImGui::GetWindowWidth();
    constexpr float hdr_h = 34.0f;

    // Card header band
    dl->AddRectFilled(wp, ImVec2(wp.x + ww, wp.y + hdr_h), BG_CARD_TOP, 8.0f, ImDrawFlags_RoundCornersTop);
    dl->AddLine(ImVec2(wp.x, wp.y + hdr_h), ImVec2(wp.x + ww, wp.y + hdr_h), BORDER_COL, 1.0f);

    // Accent bar + Bold Title
    dl->AddRectFilled(ImVec2(wp.x + 12.0f, wp.y + 10.0f), ImVec2(wp.x + 15.0f, wp.y + hdr_h - 10.0f), AccU32(1.0f), 2.0f);
    ImFont* hdr_font = ui_bold_font ? ui_bold_font : ImGui::GetFont();
    ImVec2 ts = hdr_font->CalcTextSizeA(hdr_font->FontSize, FLT_MAX, 0.0f, title);
    dl->AddText(hdr_font, hdr_font->FontSize, ImVec2(wp.x + 22.0f, wp.y + (hdr_h - ts.y) * 0.5f), TEXT_BRIGHT, title);

    if (subtitle && subtitle[0] != '\0') {
        bool is_live = (strcmp(subtitle, "ACTIVE") == 0 || strcmp(subtitle, "RENDERING") == 0 || strcmp(subtitle, "ARMED") == 0);
        ImVec2 sts = ImGui::CalcTextSize(subtitle);
        float badge_w = sts.x + 14.0f;
        float badge_h = 18.0f;
        ImVec2 bmin(wp.x + ww - badge_w - 10.0f, wp.y + (hdr_h - badge_h) * 0.5f);
        ImVec2 bmax(bmin.x + badge_w, bmin.y + badge_h);
        dl->AddRectFilled(bmin, bmax, is_live ? AccU32(0.18f) : IM_COL32(26, 26, 38, 255), 4.0f);
        dl->AddRect(bmin, bmax, is_live ? AccU32(0.55f) : BORDER_COL, 4.0f);
        dl->AddText(ImVec2(bmin.x + 7.0f, bmin.y + (badge_h - sts.y) * 0.5f), is_live ? AccLightU32(1.0f) : TEXT_DIM, subtitle);
    }

    // Inner scrollable body clipped strictly below hdr_h
    ImGui::SetCursorPos(ImVec2(0.0f, hdr_h + 1.0f));
    char body_id[96];
    sprintf_s(body_id, "%s_body", id);
    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0, 0, 0, 0));
    ImGui::PushStyleVar(ImGuiStyleVar_ChildBorderSize, 0.0f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(14.0f, 12.0f));
    ImGui::BeginChild(body_id, ImVec2(size.x, size.y - hdr_h - 1.0f), false, ImGuiWindowFlags_AlwaysUseWindowPadding);
    ImGui::PopStyleVar(2);
    ImGui::PopStyleColor();
}

inline void EndCard() {
    ImGui::EndChild(); // inner scrollable body
    ImGui::EndChild(); // outer card container
}

inline void SectionLabel(const char* text) {
    auto* dl = ImGui::GetWindowDrawList();
    ImVec2 cp = ImGui::GetCursorScreenPos();
    dl->AddCircleFilled(ImVec2(cp.x + 4.0f, cp.y + 8.0f), 2.5f, AccU32(0.95f), 12);
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + 13.0f);
    ImVec4 acc = MenuAccent.Value;
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(acc.x * 0.85f + 0.15f, acc.y * 0.85f + 0.15f, acc.z * 0.85f + 0.15f, 0.95f));
    ImGui::TextUnformatted(text);
    ImGui::PopStyleColor();
    ImGui::Spacing();
}

inline void SectionSep() {
    ImGui::Spacing();
    auto* dl = ImGui::GetWindowDrawList();
    ImVec2 p = ImGui::GetCursorScreenPos();
    float w = ImGui::GetContentRegionAvail().x;
    dl->AddLine(ImVec2(p.x, p.y), ImVec2(p.x + w, p.y), BORDER_COL, 1.0f);
    ImGui::Dummy(ImVec2(0.0f, 5.0f));
}

inline bool Chk(const char* label, bool* v) {
    return ImGui::Checkbox(label, v);
}

inline bool Sld(const char* label, float* v, float mn, float mx, const char* fmt = "%.1f") {
    return ImGui::SliderFloat(label, v, mn, mx, fmt);
}

inline bool SldI(const char* label, int* v, int mn, int mx, const char* fmt = "%d") {
    return ImGui::SliderInt(label, v, mn, mx, fmt);
}

inline bool Cmb(const char* label, int* v, const char* items) {
    auto* dl = ImGui::GetWindowDrawList();
    ImVec2 pos = ImGui::GetCursorScreenPos();
    float avail_w = ImGui::GetContentRegionAvail().x;
    ImVec2 lsz = ImGui::CalcTextSize(label, NULL, true);
    float combo_w = ImMin(138.0f, avail_w * 0.52f);
    float row_h = ImGui::GetFrameHeight();

    dl->AddText(ImVec2(pos.x, pos.y + (row_h - lsz.y) * 0.5f), ImColor(colors::combo::text_inactive), label, ImGui::FindRenderedTextEnd(label));

    ImGui::SetCursorScreenPos(ImVec2(pos.x + avail_w - combo_w, pos.y));
    ImGui::PushItemWidth(combo_w);
    char cid[96];
    sprintf_s(cid, "##cmb_%s", label);
    bool r = ImGui::Combo(cid, v, items);
    ImGui::PopItemWidth();
    return r;
}

inline bool KeyPill(const char* label, int* key_idx) {
    static const char* const kNames[] = { "LMB", "RMB", "Mouse 5", "Mouse 4", "L-Shift", "L-Ctrl", "L-Alt" };
    constexpr int kCount = 7;
    int cur = (*key_idx >= 0 && *key_idx < kCount) ? *key_idx : 0;

    auto* dl = ImGui::GetWindowDrawList();
    ImVec2 pos = ImGui::GetCursorScreenPos();
    float avail_w = ImGui::GetContentRegionAvail().x;
    ImVec2 lsz = ImGui::CalcTextSize(label, NULL, true);
    float row_h = 24.0f;

    dl->AddText(ImVec2(pos.x, pos.y + (row_h - lsz.y) * 0.5f), ImColor(colors::combo::text_active), label, ImGui::FindRenderedTextEnd(label));

    char badge[32];
    sprintf_s(badge, "[ %s ]", kNames[cur]);
    ImVec2 bsz = ImGui::CalcTextSize(badge);
    float pill_w = ImMax(bsz.x + 18.0f, 76.0f);
    ImVec2 pmin(pos.x + avail_w - pill_w, pos.y + 1.0f);
    ImVec2 pmax(pos.x + avail_w, pos.y + row_h - 1.0f);

    char btn_id[64];
    sprintf_s(btn_id, "##kp_%s", label);
    ImGui::SetCursorScreenPos(pmin);
    bool clicked = ImGui::InvisibleButton(btn_id, ImVec2(pill_w, row_h - 2.0f));
    bool hov = ImGui::IsItemHovered();

    char pop_id[64];
    sprintf_s(pop_id, "##kpop_%s", label);
    if (clicked) {
        ImGui::OpenPopup(pop_id);
    }

    dl->AddRectFilled(pmin, pmax, hov ? BG_FRAME_HOV : BG_FRAME, 5.0f);
    dl->AddRect(pmin, pmax, hov ? AccU32(0.8f) : BORDER_COL, 5.0f);
    dl->AddText(ImVec2(pmin.x + (pill_w - bsz.x) * 0.5f, pmin.y + ((row_h - 2.0f) - bsz.y) * 0.5f), AccLightU32(1.0f), badge);

    bool changed = false;
    if (ImGui::BeginPopup(pop_id)) {
        for (int i = 0; i < kCount; i++) {
            bool sel = (cur == i);
            if (ImGui::Selectable(kNames[i], sel)) {
                *key_idx = i;
                changed = true;
            }
        }
        ImGui::EndPopup();
    }
    return changed;
}

inline void ClrPick(const char* label, ImVec4* col) {
    auto* dl = ImGui::GetWindowDrawList();
    ImVec2 pos = ImGui::GetCursorScreenPos();
    float avail_w = ImGui::GetContentRegionAvail().x;
    ImVec2 lsz = ImGui::CalcTextSize(label, NULL, true);
    float frame_h = ImGui::GetFrameHeight();

    dl->AddText(ImVec2(pos.x, pos.y + (frame_h - lsz.y) * 0.5f), ImColor(colors::checkbox::text_active), label, ImGui::FindRenderedTextEnd(label));

    ImGui::SetCursorScreenPos(ImVec2(pos.x + avail_w - frame_h, pos.y));
    char id[64];
    sprintf_s(id, "##cp_%s", label);
    ImGui::ColorEdit4(id, (float*)col,
        ImGuiColorEditFlags_NoInputs | ImGuiColorEditFlags_NoLabel |
        ImGuiColorEditFlags_AlphaBar | ImGuiColorEditFlags_AlphaPreview |
        ImGuiColorEditFlags_NoSidePreview);
}

inline void SegmentedPills(const char* id_prefix, const char* const* labels, int count, int* active_idx) {
    auto* dl = ImGui::GetWindowDrawList();
    ImVec2 pos = ImGui::GetCursorScreenPos();
    float avail_w = ImGui::GetContentRegionAvail().x;
    float h = 26.0f;
    float gap = 4.0f;
    float item_w = (avail_w - gap * (count - 1)) / (float)count;

    dl->AddRectFilled(pos, ImVec2(pos.x + avail_w, pos.y + h), IM_COL32(12, 12, 18, 255), 6.0f);
    dl->AddRect(pos, ImVec2(pos.x + avail_w, pos.y + h), BORDER_COL, 6.0f);

    for (int i = 0; i < count; i++) {
        ImVec2 imin(pos.x + i * (item_w + gap) + 2.0f, pos.y + 2.0f);
        ImVec2 imax(imin.x + item_w - 4.0f, pos.y + h - 2.0f);
        char bid[64];
        sprintf_s(bid, "##seg_%s_%d", id_prefix, i);
        ImGui::SetCursorScreenPos(imin);
        if (ImGui::InvisibleButton(bid, ImVec2(imax.x - imin.x, imax.y - imin.y))) {
            *active_idx = i;
        }
        bool hov = ImGui::IsItemHovered();
        bool act = (*active_idx == i);

        if (act) {
            dl->AddRectFilled(imin, imax, AccU32(0.28f), 4.5f);
            dl->AddRect(imin, imax, AccU32(0.75f), 4.5f);
        } else if (hov) {
            dl->AddRectFilled(imin, imax, IM_COL32(255, 255, 255, 12), 4.5f);
        }

        ImVec2 tsz = ImGui::CalcTextSize(labels[i]);
        ImU32 tcol = act ? TEXT_BRIGHT : (hov ? TEXT_MID : TEXT_DIM);
        dl->AddText(ImVec2(imin.x + (imax.x - imin.x - tsz.x) * 0.5f, imin.y + (imax.y - imin.y - tsz.y) * 0.5f), tcol, labels[i]);
    }
    ImGui::SetCursorScreenPos(ImVec2(pos.x, pos.y + h + 8.0f));
}

inline void DrawTabIcon(ImDrawList* dl, int idx, ImVec2 center, ImU32 col) {
    switch (idx) {
    case 0: // Combat: Precision Crosshair
        dl->AddCircle(center, 6.5f, col, 16, 1.4f);
        dl->AddCircleFilled(center, 1.8f, col, 8);
        dl->AddLine(ImVec2(center.x - 9.0f, center.y), ImVec2(center.x - 4.5f, center.y), col, 1.3f);
        dl->AddLine(ImVec2(center.x + 4.5f, center.y), ImVec2(center.x + 9.0f, center.y), col, 1.3f);
        dl->AddLine(ImVec2(center.x, center.y - 9.0f), ImVec2(center.x, center.y - 4.5f), col, 1.3f);
        dl->AddLine(ImVec2(center.x, center.y + 4.5f), ImVec2(center.x, center.y + 9.0f), col, 1.3f);
        break;
    case 1: // Visuals: Eye / Optic
        dl->AddQuad(
            ImVec2(center.x - 8.5f, center.y),
            ImVec2(center.x, center.y - 5.5f),
            ImVec2(center.x + 8.5f, center.y),
            ImVec2(center.x, center.y + 5.5f), col, 1.4f);
        dl->AddCircleFilled(center, 2.5f, col, 12);
        break;
    case 2: // Trigger: Lightning / Pulse
        dl->AddTriangleFilled(
            ImVec2(center.x + 1.5f, center.y - 8.0f),
            ImVec2(center.x - 5.5f, center.y + 1.0f),
            ImVec2(center.x + 0.5f, center.y + 1.0f), col);
        dl->AddTriangleFilled(
            ImVec2(center.x - 1.5f, center.y + 8.0f),
            ImVec2(center.x + 5.5f, center.y - 1.0f),
            ImVec2(center.x - 0.5f, center.y - 1.0f), col);
        break;
    case 3: // Misc: 4-Node Grid
        dl->AddRect(ImVec2(center.x - 7.0f, center.y - 7.0f), ImVec2(center.x - 1.5f, center.y - 1.5f), col, 1.5f, 0, 1.3f);
        dl->AddRect(ImVec2(center.x + 1.5f, center.y - 7.0f), ImVec2(center.x + 7.0f, center.y - 1.5f), col, 1.5f, 0, 1.3f);
        dl->AddRect(ImVec2(center.x - 7.0f, center.y + 1.5f), ImVec2(center.x - 1.5f, center.y + 7.0f), col, 1.5f, 0, 1.3f);
        dl->AddRect(ImVec2(center.x + 1.5f, center.y + 1.5f), ImVec2(center.x + 7.0f, center.y + 7.0f), col, 1.5f, 0, 1.3f);
        break;
    case 4: // Settings: Tuning Sliders
        dl->AddLine(ImVec2(center.x - 7.5f, center.y - 4.5f), ImVec2(center.x + 7.5f, center.y - 4.5f), col, 1.3f);
        dl->AddCircleFilled(ImVec2(center.x - 2.5f, center.y - 4.5f), 2.5f, col, 10);
        dl->AddLine(ImVec2(center.x - 7.5f, center.y + 4.5f), ImVec2(center.x + 7.5f, center.y + 4.5f), col, 1.3f);
        dl->AddCircleFilled(ImVec2(center.x + 3.0f, center.y + 4.5f), 2.5f, col, 10);
        break;
    }
}

// ============================================================
//  MAIN MENU — ABLE INTERFACE
// ============================================================
inline void draw_menu() {
    SPOOF_FUNC;
    apply_theme();

    bool pushed_ui_font = false;
    if (ui_font) {
        ImGui::PushFont(ui_font);
        pushed_ui_font = true;
    }

    constexpr float W = 900.0f;
    constexpr float H = 580.0f;
    constexpr float SIDE = 176.0f;
    constexpr float TOP_BAR = 44.0f;
    constexpr float PAD = 14.0f;

    enum Tab { Combat, Visuals, Trigger, Misc, Settings };
    const char* tabs[] = { "Combat", "Visuals", "Trigger", "Misc", "Settings" };
    const char* tab_desc[] = {
        "Aimbot, Weapons & Reticle",
        "Player ESP, World & Palette",
        "Automated Shotgun Trigger",
        "HUD Telemetry & Security",
        "Interface Theme & Config"
    };
    constexpr int tab_n = 5;

    ImGui::SetNextWindowSize(ImVec2(W, H));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0.0f, 0.0f));
    ImGui::Begin(_("##MainWnd"), 0,
        ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoScrollbar |
        ImGuiWindowFlags_NoResize   | ImGuiWindowFlags_NoCollapse);
    ImGui::PopStyleVar();
    {
        auto* dl = ImGui::GetWindowDrawList();
        ImVec2 p = ImGui::GetWindowPos();

        // ---- Left Sidebar Surface ----
        dl->AddRectFilled(
            ImVec2(p.x, p.y),
            ImVec2(p.x + SIDE, p.y + H),
            BG_SIDEBAR, 10.0f, ImDrawFlags_RoundCornersLeft
        );
        dl->AddLine(
            ImVec2(p.x + SIDE, p.y),
            ImVec2(p.x + SIDE, p.y + H),
            BORDER_COL, 1.0f
        );

        // ---- Top Header Surface ----
        dl->AddRectFilled(
            ImVec2(p.x + SIDE + 1.0f, p.y),
            ImVec2(p.x + W, p.y + TOP_BAR),
            BG_HEADER, 10.0f, ImDrawFlags_RoundCornersTopRight
        );
        dl->AddLine(
            ImVec2(p.x + SIDE, p.y + TOP_BAR),
            ImVec2(p.x + W, p.y + TOP_BAR),
            BORDER_COL, 1.0f
        );

        // ---- Outer 1px Frame Border & Top Accent Glow Strip ----
        dl->AddRect(p, ImVec2(p.x + W, p.y + H), BORDER_COL, 10.0f, 0, 1.0f);
        dl->AddRectFilledMultiColor(
            ImVec2(p.x + 8.0f, p.y),
            ImVec2(p.x + W - 8.0f, p.y + 2.0f),
            AccU32(0.95f), AccLightU32(0.95f), AccU32(0.35f), AccU32(0.95f)
        );

        // ---- Brand Emblem in Sidebar ----
        ImVec2 badge_min(p.x + 16.0f, p.y + 16.0f);
        ImVec2 badge_max(p.x + 44.0f, p.y + 44.0f);
        dl->AddRectFilled(badge_min, badge_max, AccU32(0.20f), 7.0f);
        dl->AddRect(badge_min, badge_max, AccU32(0.75f), 7.0f, 0, 1.2f);
        ImVec2 bcenter((badge_min.x + badge_max.x) * 0.5f, (badge_min.y + badge_max.y) * 0.5f);
        dl->AddTriangleFilled(
            ImVec2(bcenter.x, bcenter.y - 7.0f),
            ImVec2(bcenter.x - 6.5f, bcenter.y + 6.0f),
            ImVec2(bcenter.x + 6.5f, bcenter.y + 6.0f),
            AccLightU32(1.0f)
        );
        dl->AddTriangleFilled(
            ImVec2(bcenter.x, bcenter.y - 2.0f),
            ImVec2(bcenter.x - 3.5f, bcenter.y + 6.0f),
            ImVec2(bcenter.x + 3.5f, bcenter.y + 6.0f),
            BG_SIDEBAR
        );

        ImFont* hdr_bold = ui_bold_font ? ui_bold_font : ImGui::GetFont();
        dl->AddText(hdr_bold, hdr_bold->FontSize, ImVec2(p.x + 54.0f, p.y + 15.0f), TEXT_BRIGHT, "ABLE");
        dl->AddText(ImVec2(p.x + 54.0f, p.y + 31.0f), TEXT_DIM, "immortal // fn");

        dl->AddLine(
            ImVec2(p.x + 14.0f, p.y + 58.0f),
            ImVec2(p.x + SIDE - 14.0f, p.y + 58.0f),
            BORDER_COL, 1.0f
        );

        // ---- Navigation Category Label ----
        dl->AddText(ImVec2(p.x + 18.0f, p.y + 68.0f), TEXT_DIM, "NAVIGATION");

        // ---- Sidebar Tab Buttons ----
        float ty_start = 90.0f;
        float th = 38.0f;
        float tgap = 5.0f;

        for (int i = 0; i < tab_n; i++) {
            float ty = ty_start + i * (th + tgap);
            bool active = (idfk::m_MenuTab == i);

            ImVec2 tmin(p.x + 12.0f, p.y + ty);
            ImVec2 tmax(p.x + SIDE - 12.0f, p.y + ty + th);

            ImGui::SetCursorPos(ImVec2(12.0f, ty));
            char id[16];
            sprintf_s(id, "##t%d", i);
            if (ImGui::InvisibleButton(id, ImVec2(SIDE - 24.0f, th)))
                idfk::m_MenuTab = i;
            bool hov = ImGui::IsItemHovered();

            if (active) {
                dl->AddRectFilled(tmin, tmax, AccU32(0.16f), 7.0f);
                dl->AddRect(tmin, tmax, AccU32(0.45f), 7.0f);
                dl->AddRectFilled(
                    ImVec2(p.x + 12.0f, p.y + ty + 8.0f),
                    ImVec2(p.x + 15.5f, p.y + ty + th - 8.0f),
                    AccLightU32(1.0f), 3.0f
                );
            } else if (hov) {
                dl->AddRectFilled(tmin, tmax, IM_COL32(255, 255, 255, 9), 7.0f);
            }

            ImU32 icol = active ? AccLightU32(1.0f) : (hov ? TEXT_MID : TEXT_DIM);
            ImU32 tcol = active ? TEXT_BRIGHT : (hov ? TEXT_MID : TEXT_DIM);

            DrawTabIcon(dl, i, ImVec2(p.x + 31.0f, p.y + ty + th * 0.5f), icol);

            ImVec2 ts = ImGui::CalcTextSize(tabs[i]);
            dl->AddText(
                ImVec2(p.x + 48.0f, p.y + ty + (th - ts.y) * 0.5f),
                tcol, tabs[i]
            );
        }

        // ---- Sidebar Footer Telemetry Card ----
        ImVec2 fmin(p.x + 12.0f, p.y + H - 68.0f);
        ImVec2 fmax(p.x + SIDE - 12.0f, p.y + H - 12.0f);
        dl->AddRectFilled(fmin, fmax, BG_CHILD, 7.0f);
        dl->AddRect(fmin, fmax, BORDER_COL, 7.0f);

        dl->AddCircleFilled(ImVec2(fmin.x + 13.0f, fmin.y + 16.0f), 3.5f, IM_COL32(52, 211, 153, 255), 12);
        dl->AddText(ImVec2(fmin.x + 23.0f, fmin.y + 8.0f), TEXT_BRIGHT, "v39.50 LIVE");

        char tele_buf[64];
        sprintf_s(tele_buf, "%.0f FPS  |  VIS: %d", ImGui::GetIO().Framerate, numVisiblePeople);
        dl->AddText(ImVec2(fmin.x + 12.0f, fmin.y + 30.0f), TEXT_DIM, tele_buf);

        // ---- Top Header Breadcrumb & Quick Actions ----
        ImVec2 active_tab_sz = hdr_bold->CalcTextSizeA(hdr_bold->FontSize, FLT_MAX, 0.0f, tabs[idfk::m_MenuTab]);
        float top_text_y = p.y + (TOP_BAR - active_tab_sz.y) * 0.5f;
        dl->AddText(hdr_bold, hdr_bold->FontSize, ImVec2(p.x + SIDE + 18.0f, top_text_y), AccLightU32(1.0f), tabs[idfk::m_MenuTab]);
        dl->AddText(ImVec2(p.x + SIDE + 26.0f + active_tab_sz.x, top_text_y), TEXT_DIM, "//");
        dl->AddText(ImVec2(p.x + SIDE + 44.0f + active_tab_sz.x, top_text_y), TEXT_MID, tab_desc[idfk::m_MenuTab]);

        ImGui::SetCursorPos(ImVec2(W - 166.0f, 8.0f));
        if (ImGui::Button("Save##top", ImVec2(72.0f, 28.0f)))
            savecfg("C:\\info");
        ImGui::SameLine(0, 6.0f);
        if (ImGui::Button("Load##top", ImVec2(72.0f, 28.0f)))
            loadcfg("C:\\info");

        // ---- Main Content Workspace ----
        float cx = SIDE + PAD;
        float cy = TOP_BAR + PAD;
        float cw = W - SIDE - PAD * 2.0f;
        float ch = H - TOP_BAR - PAD * 2.0f;
        float half_w = (cw - PAD) * 0.5f;

        ImGui::PushStyleVar(ImGuiStyleVar_ChildRounding, 8.0f);

        switch (idfk::m_MenuTab) {

        // ============================
        //  COMBAT
        // ============================
        case Tab::Combat:
        {
            ImGui::SetCursorPos(ImVec2(cx, cy));
            BeginCard("##cb_l", "AIMBOT CORE", c_enable ? "ACTIVE" : "STANDBY", ImVec2(half_w, ch));
            {
                Chk("Enable Aimbot##aim", &c_enable);
                Chk("Visible Check", &c_visible_check);
                Chk("Prediction", &c_prediction);
                Chk("Humanization", &c_humanized);
                Chk("Dead Zone", &c_dead_zone);
                Chk("Ignore Knocked", &c_knocked);
                Chk("Show FOV Circle", &c_circle);

                SectionSep();
                SectionLabel("Targeting & Bindings");
                KeyPill("Aim Activation", &c_aim_key);
                ImGui::Spacing();
                Cmb("Smoothing Curve", &c_aim1, "Linear\0Bezier\0Humanized\0\0");
                ImGui::Spacing();
                Cmb("Target Hitbox", &c_hitbox, "Head\0Neck\0Pelvis\0Random\0\0");

                SectionSep();
                SectionLabel("Target Lock & Exploits");
                Chk("Target Stick", &c_target_stick);
                if (c_target_stick) {
                    SldI("Stick Timeout (ms)##ms", &c_stick_timeout_ms, 1000, 15000, "%d ms");
                }
                Sld("Micro Aim Shake", &c_aim_shake, 0.0f, 3.0f, "%.2f");
                Chk("No Recoil", &C_Exploits::c_no_recoil);
                Chk("No Spread", &C_Exploits::c_no_spread);
                Chk("Smart Target Scoring", &C_Exploits::c_smart_scoring);
                Chk("Bone Oscillation", &C_Exploits::c_bone_oscillation);
                if (C_Exploits::c_bone_oscillation) {
                    Sld("Oscillation Speed", &C_Exploits::c_oscillation_speed, 0.5f, 8.0f, "%.1fx");
                }
            }
            EndCard();

            ImGui::SetCursorPos(ImVec2(cx + half_w + PAD, cy));
            BeginCard("##cb_r", "TUNING & WEAPON PROFILES", "PRECISION", ImVec2(half_w, ch));
            {
                Sld("Global Smoothness", &c_smooth, 1.0f, 20.0f, "%.1f");
                Sld("FOV Radius", &c_fov, 1.0f, 300.0f, "%.0f px");
                Sld("Dead Zone Threshold", &c_custom_dead_zone, 0.0f, 1.0f, "%.2f");
                SldI("Engagement Distance", &max_trig_distance, 0, 50, "%d m");

                SectionSep();
                SectionLabel("Per-Weapon Override");
                Chk("Enable Weapon Profiles", &rGunConfigs);
                ImGui::Spacing();

                static const char* const wpn_tabs[] = { "Shotgun", "SMG", "Rifle", "Sniper" };
                SegmentedPills("wpn", wpn_tabs, 4, &idfk::m_WeaponSubTab);

                if (idfk::m_WeaponSubTab == 0) {
                    Chk("Shotgun Prediction##sg", &weapon_cfg::shotgun::rPrediction);
                    SldI("Shotgun Smooth##sg", &weapon_cfg::shotgun::rSmooth, 1, 20);
                    SldI("Shotgun FOV##sg", &weapon_cfg::shotgun::rFovSize, 1, 1000, "%d px");
                } else if (idfk::m_WeaponSubTab == 1) {
                    Chk("SMG Prediction##smg", &weapon_cfg::smg::rPrediction);
                    SldI("SMG Smooth##smg", &weapon_cfg::smg::rSmooth, 1, 20);
                    SldI("SMG FOV##smg", &weapon_cfg::smg::rFovSize, 1, 1000, "%d px");
                } else if (idfk::m_WeaponSubTab == 2) {
                    Chk("Rifle Prediction##ar", &weapon_cfg::rifle::rPrediction);
                    SldI("Rifle Smooth##ar", &weapon_cfg::rifle::rSmooth, 1, 20);
                    SldI("Rifle FOV##ar", &weapon_cfg::rifle::rFovSize, 1, 1000, "%d px");
                } else {
                    Chk("Sniper Prediction##snp", &weapon_cfg::sniper::rPrediction);
                    SldI("Sniper Smooth##snp", &weapon_cfg::sniper::rSmooth, 1, 20);
                    SldI("Sniper FOV##snp", &weapon_cfg::sniper::rFovSize, 1, 1000, "%d px");
                }

                SectionSep();
                static const char* const vis_sub[] = { "Crosshair", "Target Visuals" };
                SegmentedPills("cbvis", vis_sub, 2, &idfk::m_CombatVisSubTab);

                if (idfk::m_CombatVisSubTab == 0) {
                    Chk("Custom Reticle", &C_Crosshair::enabled);
                    if (C_Crosshair::enabled) {
                        Sld("Reticle Radius##xh", &C_Crosshair::radius, 2.0f, 20.0f, "%.1f px");
                        Sld("Stroke Thickness##xh", &C_Crosshair::thickness, 0.5f, 5.0f, "%.1f px");
                        SldI("Circle Segments##xh", &C_Crosshair::num_segments, 4, 16);
                        Chk("Drop Outline##xh", &C_Crosshair::outline);
                        ClrPick("Reticle Color", (ImVec4*)C_Crosshair::color);
                    }
                } else {
                    Chk("Aim Curve (Bezier)", &C_TargetVisuals::aim_curve);
                    Chk("Target Lock Dot", &C_TargetVisuals::target_dot);
                    Chk("Target Snap Line", &C_TargetVisuals::target_line);
                    Chk("Deadzone Ring", &C_TargetVisuals::deadzone_dot);
                    if (C_TargetVisuals::deadzone_dot) {
                        Sld("Deadzone Radius##tv", &C_TargetVisuals::restriction, 1.0f, 15.0f, "%.1f px");
                    }
                    Chk("Prediction Marker (X)", &C_TargetVisuals::prediction_x);
                }
            }
            EndCard();
            break;
        }

        // ============================
        //  VISUALS
        // ============================
        case Tab::Visuals:
        {
            ImGui::SetCursorPos(ImVec2(cx, cy));
            BeginCard("##vs_l", "PLAYER ESP & OVERLAY", c_enable_esp ? "RENDERING" : "DISABLED", ImVec2(half_w, ch));
            {
                Chk("Master ESP Switch", &c_enable_esp);

                SectionSep();
                SectionLabel("Bounding & Geometry");
                Chk("Bounding Box", &c_enable_box);
                if (c_enable_box) {
                    Cmb("Box Style", &c_box, "Static\0Filled\0Cornered\0\0");
                    Chk("Backdrop Fill", &c_filled_box);
                }
                Chk("Bone Skeleton", &c_skeleton);
                if (c_skeleton) {
                    Chk("Bezier Bone Curves##skel", &C_Exploits::c_bezier_skeleton);
                    if (C_Exploits::c_bezier_skeleton) {
                        Sld("Curve Tension##str", &C_Exploits::c_bezier_curve_strength, 0.05f, 0.5f, "%.2f");
                    }
                }
                Chk("Snaplines", &c_lines);
                Chk("Head Circle", &c_headcircle);
                Chk("Player Chams", &c_charms);
                Chk("Wireframe Mesh", &c_wireframe);

                SectionSep();
                SectionLabel("Telemetry Flags");
                Chk("Distance Readout", &c_distance);
                Chk("Equipped Weapon", &c_weapon);
                Chk("Player Username", &c_username);
                Chk("Competitive Rank", &c_rank_esp);
                Chk("Platform Icon", &c_platform);
                Chk("Movement State", &C_Movement::show_movement);
                if (C_Movement::show_movement) {
                    Chk("Dynamic State Color", &C_Movement::show_state_color);
                }

                SectionSep();
                SectionLabel("Tactical Indicators");
                Chk("Out-of-FOV Arrows", &c_arrows);
                Chk("Active Target Line", &c_target);
                Chk("Bullet Tracers", &C_Exploits::c_bullet_tracers);
                if (C_Exploits::c_bullet_tracers) {
                    Cmb("Tracer Style##trc", &C_Exploits::c_tracer_style, "Line\0Fade\0Gradient\0\0");
                    Sld("Fade Duration##trc", &C_Exploits::c_tracer_duration, 0.5f, 5.0f, "%.1f s");
                }
                Chk("2D Minimap Radar", &c_radar);
                Chk("Threat / Risk Alert", &c_threat);
            }
            EndCard();

            ImGui::SetCursorPos(ImVec2(cx + half_w + PAD, cy));
            BeginCard("##vs_r", "PALETTE & GEOMETRY", "STYLING", ImVec2(half_w, ch));
            {
                SectionLabel("Color Channels");
                static const char* const clr_tabs[] = { "Core & Aim", "Visible ESP", "Occluded ESP" };
                SegmentedPills("clrtab", clr_tabs, 3, &idfk::m_ColorSubTab);

                if (idfk::m_ColorSubTab == 0) {
                    ClrPick("Primary Visible", &c_color_visible);
                    ClrPick("Primary Occluded", &c_color_invisible);
                    SectionSep();
                    ClrPick("FOV Ring", &FovColor);
                    ClrPick("FOV Interior Fill", &FovFillColor);
                    ClrPick("Locked Aimline", &AimlineColor);
                } else if (idfk::m_ColorSubTab == 1) {
                    ClrPick("Bounding Box##v", &BoxColorVis);
                    ClrPick("Box Interior Fill##v", &FillColorVis);
                    ClrPick("Bone Skeleton##v", &SkeletonColorVis);
                    ClrPick("Snaplines##v", &SnaplineColorVis);
                    ClrPick("Distance Tag##v", &DistanceColorVis);
                    ClrPick("Username Tag##v", &UsernameColorVis);
                    ClrPick("Kills Counter##v", &KillsColorVis);
                    ClrPick("Weapon Label##v", &WeaponColorVis);
                    ClrPick("FOV Arrows##v", &FovArrowsColorVis);
                } else {
                    ClrPick("Bounding Box##h", &BoxColorHidden);
                    ClrPick("Box Interior Fill##h", &FillColorHidden);
                    ClrPick("Bone Skeleton##h", &SkeletonColorHidden);
                    ClrPick("Snaplines##h", &SnaplineColorHidden);
                    ClrPick("Distance Tag##h", &DistanceColorHidden);
                    ClrPick("Username Tag##h", &UsernameColorHidden);
                    ClrPick("Kills Counter##h", &KillsColorHidden);
                    ClrPick("Weapon Label##h", &WeaponColorHidden);
                    ClrPick("FOV Arrows##h", &FovArrowsColorHidden);
                }

                SectionSep();
                SectionLabel("Render Geometry");
                Cmb("Snapline Origin", &c_line_start, "Top\0Center\0Bottom\0\0");
                ImGui::Spacing();
                Cmb("Snapline Anchor", &c_line_end, "Top\0Center\0Bottom\0\0");
                ImGui::Spacing();
                Sld("Cull Distance", &c_max_render_distance, 1.0f, 360.0f, "%.0f m");
                Sld("Skeleton Stroke", &c_outlined, 1.0f, 5.0f, "%.1f px");
                Sld("Box Stroke", &c_outlined1, 1.0f, 5.0f, "%.1f px");
            }
            EndCard();
            break;
        }

        // ============================
        //  TRIGGER
        // ============================
        case Tab::Trigger:
        {
            ImGui::SetCursorPos(ImVec2(cx, cy));
            BeginCard("##tr_l", "TRIGGERBOT ENGINE", c_triggggger ? "ARMED" : "STANDBY", ImVec2(half_w, ch));
            {
                Chk("Enable Shotgun Triggerbot", &c_triggggger);
                SectionSep();
                SectionLabel("Timing & Range");
                SldI("Maximum Range", &max_trig_distance, 0, 50, "%d m");
                SldI("Fire Delay", &trig_delay, 0, 5, "%d ms");
                SectionSep();
                SectionLabel("Activation");
                KeyPill("Trigger Keybind", &c_trig_key);
            }
            EndCard();

            ImGui::SetCursorPos(ImVec2(cx + half_w + PAD, cy));
            BeginCard("##tr_r", "TRIGGER TELEMETRY", "STATUS", ImVec2(half_w, ch));
            {
                SectionLabel("Operational Summary");
                ImGui::TextColored(ImVec4(0.66f, 0.64f, 0.76f, 1.0f), "Mode: Shotgun Raycast Verification");
                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.66f, 0.64f, 0.76f, 1.0f), "Effective Distance: %d meters", max_trig_distance);
                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.66f, 0.64f, 0.76f, 1.0f), "Reaction Window: %d ms", trig_delay);
            }
            EndCard();
            break;
        }

        // ============================
        //  MISC
        // ============================
        case Tab::Misc:
        {
            ImGui::SetCursorPos(ImVec2(cx, cy));
            BeginCard("##ms_l", "HUD & DISPLAY", "OVERLAY", ImVec2(half_w, ch));
            {
                SectionLabel("Game Info HUD");
                Chk("Top Telemetry Bar", &C_GameInfo::show_hud_bar);
                if (C_GameInfo::show_hud_bar) {
                    Chk("Alive Player Count", &C_GameInfo::show_player_count);
                    Chk("Elimination Count", &C_GameInfo::show_kill_count);
                    Chk("Spectator Warning", &C_GameInfo::show_spectators);
                }

                SectionSep();
                SectionLabel("Renderer & Capture");
                Chk("Framerate Counter", &C_Misc::c_show_fps);
                Chk("Vertical Sync (VSync)", &C_Overlay::c_enable_vsync);
                Chk("Stream Proof", &C_Exploits::c_stream_proof);
                Chk("Diagnostic Overlay", &debug);

                SectionSep();
                SectionLabel("Runtime Hardening");
                Chk("Anti-Dump (PE Wipe)", &C_Exploits::c_anti_dump);
                Chk("Adaptive Thread Throttle", &C_Exploits::c_thread_throttle);
                Chk("CFG Flatten", &C_Exploits::c_cfg_flatten);
            }
            EndCard();

            ImGui::SetCursorPos(ImVec2(cx + half_w + PAD, cy));
            BeginCard("##ms_r", "PROFILES & SESSION", "STORAGE", ImVec2(half_w, ch));
            {
                SectionLabel("Configuration Persistence");
                float btn_w = (ImGui::GetContentRegionAvail().x - 8.0f) * 0.5f;
                if (ImGui::Button("Save Profile##cfg", ImVec2(btn_w, 32.0f)))
                    savecfg("C:\\info");
                ImGui::SameLine(0, 8.0f);
                if (ImGui::Button("Load Profile##cfg", ImVec2(btn_w, 32.0f)))
                    loadcfg("C:\\info");

                SectionSep();
                SectionLabel("Session Diagnostics");
                ImGui::TextColored(ImVec4(0.68f, 0.66f, 0.78f, 1.0f), "Target Build: v39.50");
                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.68f, 0.66f, 0.78f, 1.0f), "Visible Entities: %d", numVisiblePeople);
                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.68f, 0.66f, 0.78f, 1.0f), "Frame Time: %.2f ms", 1000.0f / ImMax(1.0f, ImGui::GetIO().Framerate));
            }
            EndCard();
            break;
        }

        // ============================
        //  SETTINGS
        // ============================
        case Tab::Settings:
        {
            ImGui::SetCursorPos(ImVec2(cx, cy));
            BeginCard("##st_l", "APPEARANCE & ACCENT", "ABLE UI", ImVec2(half_w, ch));
            {
                SectionLabel("Dynamic Theme Accent");
                ClrPick("Menu Accent Color", &MenuAccent.Value);

                SectionSep();
                SectionLabel("Curated Presets");
                struct Preset { const char* name; ImVec4 col; };
                static const Preset presets[] = {
                    { "Able Electric Violet", ImVec4(0.541f, 0.361f, 0.965f, 1.0f) },
                    { "Glacier Cyan",         ImVec4(0.024f, 0.714f, 0.831f, 1.0f) },
                    { "Crimson Pulse",        ImVec4(0.957f, 0.247f, 0.369f, 1.0f) },
                    { "Emerald Matrix",       ImVec4(0.063f, 0.725f, 0.506f, 1.0f) },
                    { "Amber Horizon",        ImVec4(0.961f, 0.620f, 0.043f, 1.0f) }
                };
                float full_w = ImGui::GetContentRegionAvail().x;
                for (int i = 0; i < 5; i++) {
                    if (ImGui::Button(presets[i].name, ImVec2(full_w, 28.0f))) {
                        MenuAccent.Value = presets[i].col;
                        AccentColor = presets[i].col;
                    }
                }
            }
            EndCard();

            ImGui::SetCursorPos(ImVec2(cx + half_w + PAD, cy));
            BeginCard("##st_r", "INTERFACE SPECIFICATION", "BUILD", ImVec2(half_w, ch));
            {
                SectionLabel("Able UI Engine");
                ImGui::TextColored(ImVec4(0.68f, 0.66f, 0.78f, 1.0f), "Renderer: DirectX 11 Immediate");
                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.68f, 0.66f, 0.78f, 1.0f), "Toggle Key: TAB");
                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.68f, 0.66f, 0.78f, 1.0f), "Profile Path: C:\\info");
            }
            EndCard();
            break;
        }
        } // switch

        ImGui::PopStyleVar(1);
    }
    ImGui::End();

    if (pushed_ui_font) {
        ImGui::PopFont();
    }
}
