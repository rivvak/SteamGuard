#include "../../../Kernel/driver.hpp"
#include "../../../../Includes/Definitions/hex_definitions.hpp"

struct WeaponInformation
{
	int32_t ammo_count;
	int32_t max_ammo;

	BYTE tier;
	std::string weapon_name;
	std::string buildplan;

};
std::string LocalPlayerWeapon;


WeaponInformation WeaponInfo;

// ============================================================================
//  Full Bone Enum (Popstar / UE5 standard skeleton)
//  Sequential indices matching UE skeletal mesh bone array
// ============================================================================
enum e_bone : int {
	root = 0,
	bone_attach = 1,
	pelvis = 2,
	spine_01 = 3,
	spine_02 = 4,
	spine_03 = 5,
	spine_04 = 6,
	spine_05 = 7,
	clavicle_l = 8,
	upperarm_l = 9,
	lowerarm_l = 10,
	hand_l = 11,
	index_metacarpal_l = 12,
	index_01_l = 13,
	index_02_l = 14,
	index_03_l = 15,
	middle_metacarpal_l = 16,
	middle_01_l = 17,
	middle_02_l = 18,
	middle_03_l = 19,
	pinky_metacarpal_l = 20,
	pinky_01_l = 21,
	pinky_02_l = 22,
	pinky_03_l = 23,
	ring_metacarpal_l = 24,
	ring_01_l = 25,
	ring_02_l = 26,
	ring_03_l = 27,
	thumb_01_l = 28,
	thumb_02_l = 29,
	thumb_03_l = 30,
	weapon_l = 31,
	hand_attach_l = 32,
	lowerarm_twist_01_l = 33,
	lowerarm_twist_02_l = 34,
	upperarm_twist_01_l = 35,
	upperarm_twist_02_l = 36,
	clavicle_r = 37,
	upperarm_r = 38,
	lowerarm_r = 39,
	hand_r = 40,
	index_metacarpal_r = 41,
	index_01_r = 42,
	index_02_r = 43,
	index_03_r = 44,
	middle_metacarpal_r = 45,
	middle_01_r = 46,
	middle_02_r = 47,
	middle_03_r = 48,
	pinky_metacarpal_r = 49,
	pinky_01_r = 50,
	pinky_02_r = 51,
	pinky_03_r = 52,
	ring_metacarpal_r = 53,
	ring_01_r = 54,
	ring_02_r = 55,
	ring_03_r = 56,
	thumb_01_r = 57,
	thumb_02_r = 58,
	thumb_03_r = 59,
	weapon_r = 60,
	hand_attach_r = 61,
	lowerarm_twist_01_r = 62,
	lowerarm_twist_02_r = 63,
	upperarm_twist_01_r = 64,
	upperarm_twist_02_r = 65,
	neck_01 = 66,
	neck_02 = 67,
	head = 68,
	attach_backpack = 69,
	attach_cape = 70,
	thigh_l = 71,
	calf_l = 72,
	calf_twist_01_l = 73,
	calf_twist_02_l = 74,
	foot_l = 75,
	ball_l = 76,
	thigh_twist_01_l = 77,
	thigh_r = 78,
	calf_r = 79,
	calf_twist_01_r = 80,
	calf_twist_02_r = 81,
	foot_r = 82,
	ball_r = 83,
	thigh_twist_01_r = 84,
	ik_foot_root = 85,
	ik_foot_l = 86,
	ik_foot_r = 87,
	ik_hand_root = 88,
	ik_hand_gun = 89,
	ik_hand_l = 90,
	ik_hand_r = 91,
	vb_head_fx = 110
};

// Legacy aliases for backward compatibility with existing code
enum EBoneIndex : int {
	Head = e_bone::vb_head_fx,
	Neck = e_bone::neck_02,
	Chest = e_bone::neck_01,
	Pelvis = e_bone::pelvis,
	LShoulder = e_bone::upperarm_l,
	LElbow = e_bone::lowerarm_l,
	LHand = e_bone::hand_l,
	RShoulder = e_bone::upperarm_r,
	RElbow = e_bone::lowerarm_r,
	RHand = e_bone::hand_r,
	LHip = e_bone::thigh_l,
	LKnee = e_bone::calf_l,
	LFoot = e_bone::foot_l,
	RHip = e_bone::thigh_r,
	RKnee = e_bone::calf_r,
	RFoot = e_bone::foot_r,
};

// Legacy aliases (old naming)
enum bones : int {
	HumanBase = e_bone::root,
	HumanPelvis = e_bone::pelvis,
	HumanNeck = e_bone::neck_02,
	HumanHead = e_bone::vb_head_fx,
	HumanChest = e_bone::neck_01
};

// ============================================================================
//  Address validation (Popstar pattern)
// ============================================================================
inline bool is_valid_address(uint64_t address) {
	if (address == 0 || address == 0xCCCCCCCCCCCCCCCC || address == 0xFFFFFFFFFFFFFFFF)
		return false;
	if (address <= 0x400000 || address > 0x7FFFFFFFFFFFFFFF)
		return false;
	return true;
}

// ============================================================================
//  Movement mode enum (UE EMovementMode)
// ============================================================================
enum class e_movement_mode : uint8_t {
	none = 0,
	walking = 1,
	nav_walking = 2,
	falling = 3,
	swimming = 4,
	flying = 5,
	custom = 6
};

//offsets ud by KeplerOnTop


enum offset {
	// 43.00 (Fortnite+Release-43.00-CL-59596594-Windows)
	LocationUnderReticle = 0x2208,
	aactor = 0x208,      // Actors TArray
	root_component = 0x1B0,
	relative_location = 0x140,
	PrimaryPickupItemEntry = 0x3A8, // unverified in 43.00
	last_sumbit = 0x2E0,            // unverified in 43.00
	last_render = 0x2E8, // 43.00 LastRenderTime
	levels = 0x1E8,      // 43.00 Levels
	LocalPawn = 0x320,   // 43.00 LocalPawn
	bone_array = 0x660,  // 43.00 BoneArray
	pname = 0x9F0,       // 43.00 PlayerName
	SpawnSourceOverride = 0xBE8, // unverified in 43.00
	WorldTimeSeconds = 0x198,    // 43.00 Seconds (unchanged)
};
enum weaponoffsets {

	// 43.00 (Fortnite+Release-43.00-CL-59596594-Windows)
	AmmoCount = 0x1138,

	CurrentWeapon = 0x9E0,
	WeaponData = 0x650,
	Tier = 0x296,        // DisplayTier (uint8)
	ItemName = 0x38,     // WeaponData->ItemName (FText)

	// Recoil: WeaponRecoilOffset (FRotator) at 0x23A8
	RecoilVert = 0x23A8,      // pitch component (double)
	RecoilHoriz = 0x23B0,     // yaw component (double)
	RecoilRecovery = 0x23B8,  // roll component (double)

	// Spread: no published encrypted-pad offsets in 43.00 dumps
	SpreadPadA = 0x23BA,
	SpreadPadB = 0x23BB,

	// Projectile
	ProjectileSpeed = 0x21E4,
	ProjectileGravity = 0x21E8,
};

template< typename t >
class TArray
{
public:

	TArray() : tData(), iCount(), iMaxCount() { }
	TArray(t* data, int count, int max_count) :
		tData(tData), iCount(iCount), iMaxCount(iMaxCount) { }

public:
	t* tData;
	int iCount;
	int iMaxCount;
	uintptr_t Array = 0;
	uint32_t Count = 0;
	uint32_t MaxCount = 0;

	auto Get(int idx) -> t
	{
		return device_().read< t >(reinterpret_cast<__int64>(this->tData) + (idx * sizeof(t)));
	}

	auto Size() -> std::uint32_t
	{
		return this->iCount;
	}

	bool isValid() const {
		return Array && Count <= MaxCount && MaxCount <= 1000000;
	}

	bool IsValid()
	{
		return this->iCount != 0;
	}
};


uintptr_t TargetedFortPawn( ) {

	auto& cache = GameCache::Cache::getInstance( );

	readaddy( uintptr_t, cache.get_pcontroller( ).value_or( 0 ), 0x1740); // 43.00 TargetedFortPawn
}
