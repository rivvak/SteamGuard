#pragma once

#include <iostream>
#include <string>

#define _snprintf snprintf

#include "../Dependencies/Fortnite/Game/UE_Defnitions/entity_engine.hpp"

namespace offsets {
    // Current Patch: v43.00 (Fortnite+Release-43.00-CL-59596594-Windows)
    // Sources: itsunderscores/Fortnite-Offsets + cheatoffsets.com (2026-11-03)
    // NOTE: values marked UNVERIFIED were not published in 43.00 dumps.

    // Globals
    uintptr_t GWorld = 0x1C8E1578;              // encrypted - use GEngine chain
    uintptr_t GWorldXorKey = 0x0;               // UNVERIFIED: 43.00 decrypt key unpublished
    uintptr_t GWorldXorRotationCount = 0;
    uintptr_t GNames = 0x0;                     // UNVERIFIED: FNamePool not published for 43.00
    uintptr_t GEngine = 0x1C8E2F48;             // UEngine**
    uintptr_t GameViewport = 0xB70;             // UEngine::GameViewport
    uintptr_t ViewportClientWorld = 0x78;       // UGameViewportClient::World
    uintptr_t GObjects = 0x0;                   // UNVERIFIED: not published for 43.00
    uintptr_t GObjectsCount = 0x0;              // UNVERIFIED
    uintptr_t StaticFindObject = 0x0;           // UNVERIFIED: function RVA not published
    uintptr_t ProcessEvent = 0x0;               // UNVERIFIED: function RVA not published
    uintptr_t ProcessEventIndex = 0x260;        // UNVERIFIED: vtable index
    uintptr_t BoneMatrix = 0x0;                 // UNVERIFIED: function RVA not published

    // Player / Controller
    uintptr_t LocalPlayers = 0x38;
    uintptr_t PlayerController = 0x30;
    uintptr_t PlayerCameraManager = 0x330;
    uintptr_t AcknowledgedPawn = 0x320;
    uintptr_t PlayerState = 0x298;
    uintptr_t NetConnection = 0x4B0;
    uintptr_t TeamIndex = 0xF81;
    uintptr_t bIsDying = 0x738;
    uintptr_t bIsDBNO = 0x881;
    uintptr_t bIsABot = 0x282;
    uintptr_t Platform = 0x408;
    uintptr_t TargetedFortPawn = 0x1740;
    uintptr_t KillScore = 0xF98;
    uintptr_t RebootCount = 0x1894;             // UNVERIFIED
    uintptr_t PlayerName = 0x9F0;
    uintptr_t PlayerNamePrivate = 0x348;        // UNVERIFIED
    uintptr_t RankedProgress = 0xE8;            // habanero-relative (hab + 0xD8 + 0x10)
    uintptr_t PlayerAimOffset = 0x2390;         // FRotator (SDK 4302); 0x2208 is LocationUnderReticle FVector

    // World
    uintptr_t OwningGameInstance = 0x248;
    uintptr_t GameState = 0x1D0;
    uintptr_t PlayerArray = 0x290;
    uintptr_t WorldSettings = 0x2C8;
    uintptr_t WorldGravityZ = 0x330;            // UNVERIFIED
    uintptr_t WorldToMeters = 0x320;            // UNVERIFIED
    uintptr_t PersistentLevel = 0x40;
    uintptr_t Levels = 0x1E8;
    uintptr_t Seconds = 0x198;                  // UWorld::Seconds (double)

    // Actor / Pawn
    uintptr_t AActor = 0x40;                    // UNVERIFIED (PersistentLevel)
    uintptr_t AActors = 0x208;                  // 43.00 Actors TArray
    uintptr_t RootComponent = 0x1B0;
    uintptr_t PawnPrivate = 0x2F0;
    uintptr_t MoveIgnoreActors = 0x348;         // UNVERIFIED

    // Mesh / Components
    uintptr_t Mesh = 0x2F8;
    uintptr_t BoneArray = 0x660;
    uintptr_t BoneCache = 0x670;
    uintptr_t MeshDeformerInstances = 0x5C0;    // UNVERIFIED
    uintptr_t ComponentToWorld = 0x1E0;
    uintptr_t RelativeLocation = 0x140;
    uintptr_t RelativeRotation = 0x158;
    uintptr_t RelativeScale3D = 0x170;
    uintptr_t ComponentVelocity = 0x188;
    uintptr_t AdditionalAimOffset = 0x2B80;     // UNVERIFIED
    uintptr_t LastRenderTime = 0x2E8;
    uintptr_t LocationUnderReticle = 0x2208;

    // Camera
    uintptr_t CameraLocation = 0x178;
    uintptr_t CameraRotation = 0x188;
    uintptr_t CameraFOV = 0x37C;

    // Vehicles
    uintptr_t CurrentVehicle = 0x2CE0;

    // Weapons
    uintptr_t CurrentWeapon = 0x9E0;
    uintptr_t WeaponData = 0x650;
    uintptr_t WeaponOffsetCorrection = 0x23C0;  // SDK 4302 + underscores 43.00
    uintptr_t AmmoCount = 0x1138;
    uintptr_t bIsReloadingWeapon = 0x391;
    uintptr_t ReloadAnimation = 0x1628;         // UNVERIFIED
    uintptr_t LWProjectile_ActivateRemovedTimestamp = 0x2960; // UNVERIFIED
    uintptr_t ProjectileSpeed = 0x21E4;
    uintptr_t ProjectileGravity = 0x21E8;
    uintptr_t MaxTargetingAimAdjustPerSecond = 0x2388; // UNVERIFIED
    uintptr_t ServerWorldTimeSecondsDelta = 0x2E8;     // UNVERIFIED
    uintptr_t LastFireTimeVerified = 0x103C;
    uintptr_t WeaponCoreAnimation = 0x16A8;     // UNVERIFIED
    uintptr_t WeaponRecoilOffset = 0x23A8;      // FRotator

    // Items / Loot
    uintptr_t PrimaryPickupItemEntry = 0x370;   // SDK 1753 + underscores 43.00
    uintptr_t ItemName = 0x38;                  // WeaponData->ItemName (FText)
    uintptr_t ItemType = 0xA8;                  // UNVERIFIED
    uintptr_t PrimaryAssetOverride = 0xA9;      // UNVERIFIED
    uintptr_t ItemRarity = 0x296;               // DisplayTier (uint8)
    uintptr_t bAlreadySearched = 0xD52;         // UNVERIFIED

    // Misc
    uintptr_t HabaneroComponent = 0x920;

    // Legacy aliases
    uintptr_t fLastSubmitTime = 0x2E0;          // UNVERIFIED
    uintptr_t fLastRenderTimeOnScreen = 0x2E8;
    uintptr_t WorldTimeSeconds = 0x198;
    uintptr_t OwningWorld = 0xC0;
    uintptr_t Velocity = 0x188;
}
