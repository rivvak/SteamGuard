#include "../../../../Includes/includes.hpp"
#include "../UE_Defnitions/entity_engine.hpp"
#include "../UE_Defnitions/entity_enums.hpp"
#include "../../../../Includes/Definitions/c_definitions.hpp"
#include "../../../../Includes/Definitions/hex_definitions.hpp"
#include <map>
#include <unordered_map>
#include <vector>
#include <utility>

// ============================================================================
//  Per-Frame Bone Cache
//  Reads each bone once per mesh per frame, caches the world-space position.
//  Eliminates redundant driver reads (~25+ per entity for skeleton alone).
// ============================================================================
struct BoneCacheEntry {
	uint64_t frame = 0;
	uintptr_t bone_ptr = 0;
	D3DMATRIX comp_matrix = {};
	fvector cached_pos[120];
	bool has_pos[120] = {};
	bool valid = false;
};

static std::unordered_map<uintptr_t, BoneCacheEntry> g_boneCache;
static uint64_t g_frameCounter = 0;
static uintptr_t g_currentMesh = 0;
static bool g_cameraCached = false;
static bool g_w2sCached = false;
static fvector g_cachedAxisX, g_cachedAxisY, g_cachedAxisZ;
static float g_cachedFovScale = 0;

// ViewState camera debug info (displayed in debug overlay)
static uintptr_t g_dbgViewState = 0;
static double g_dbgFovScale = 0.0;
static double g_dbgMatrix[4][4] = {};
static bool g_usingViewState = false;
static const char* g_camSourceName = "None";

inline void BeginBoneFrame() {
	g_frameCounter++;
	g_cameraCached = false;
	g_w2sCached = false;
	// Evict stale cache entries every 256 frames
	if ((g_frameCounter & 0xFF) == 0) {
		for (auto it = g_boneCache.begin(); it != g_boneCache.end();) {
			if (g_frameCounter - it->second.frame > 5)
				it = g_boneCache.erase(it);
			else
				++it;
		}
	}
}

fvector location;
frotator rotation;
float fov;

struct
{
	double a;
	double b;
	double c;

} rotation_s;

uintptr_t bone_;

ftransform comp_;

// Skeleton pairs (Popstar-style, anatomically correct using named bones)
std::vector<std::pair<int, int>> bone_pairs =
{
	{e_bone::spine_05, e_bone::neck_01},     // chest â†’ neck
	{e_bone::spine_05, e_bone::upperarm_r},  // chest â†’ right shoulder
	{e_bone::spine_05, e_bone::upperarm_l},  // chest â†’ left shoulder
	{e_bone::upperarm_l, e_bone::lowerarm_l},// left upper arm â†’ forearm
	{e_bone::lowerarm_l, e_bone::hand_l},    // left forearm â†’ hand
	{e_bone::upperarm_r, e_bone::lowerarm_r},// right upper arm â†’ forearm
	{e_bone::lowerarm_r, e_bone::hand_r},    // right forearm â†’ hand
	{e_bone::spine_05, e_bone::pelvis},      // chest â†’ pelvis
	{e_bone::pelvis, e_bone::thigh_r},       // pelvis â†’ right thigh
	{e_bone::pelvis, e_bone::thigh_l},       // pelvis â†’ left thigh
	{e_bone::thigh_r, e_bone::calf_r},       // right thigh â†’ calf
	{e_bone::calf_r, e_bone::ik_foot_r},     // right calf â†’ foot
	{e_bone::thigh_l, e_bone::calf_l},       // left thigh â†’ calf
	{e_bone::calf_l, e_bone::ik_foot_l},     // left calf â†’ foot
};

namespace Game_ {

	class World_ {
	public:
		void get_bone_array(uintptr_t);

		fvector get_bone(int);

		std::unordered_map<int, fvector> get_skeleton(const std::vector<std::pair<int, int>>& bone_pairs);
	};

	class Camera_ {
	public:

		fvector2d w2s(fvector);

		void get_camera();
	};

}
inline Game_::World_ bone_manager;
inline Game_::Camera_ camera_manager;



void Game_::World_::get_bone_array(uintptr_t ent) {
	g_currentMesh = ent;
	auto& entry = g_boneCache[ent];

	// Already cached this frame - restore bone pointer, skip driver reads
	if (entry.frame == g_frameCounter && entry.valid) {
		bone_ = entry.bone_ptr;
		return;
	}

	// 43.00: BoneArray 0x660, BoneArray_cache 0x670 (underscores dump)
	bone_ = device_().read<uintptr_t>(ent + 0x660 + (device_().read<int>(ent + 0x670) * 0x10));
	comp_ = device_().read<ftransform>(ent + 0x1E0);

	entry.frame = g_frameCounter;
	entry.bone_ptr = bone_;
	entry.comp_matrix = comp_.ToMatrixWithScale();
	entry.valid = (bone_ != 0);
	memset(entry.has_pos, 0, sizeof(entry.has_pos));
}

fvector Game_::World_::get_bone(int id) {
	// Check per-frame cache first - avoids redundant driver reads
	if (id >= 0 && id < 120 && g_currentMesh) {
		auto it = g_boneCache.find(g_currentMesh);
		if (it != g_boneCache.end() && it->second.frame == g_frameCounter && it->second.valid) {
			auto& entry = it->second;
			if (entry.has_pos[id]) {
				return entry.cached_pos[id];
			}
			// Not cached yet - read once and store
			auto bone = device_().read<FTransform>(entry.bone_ptr + id * 0x70);
			D3DMATRIX bone_matrix = MatrixMultiplication(bone.ToMatrixWithScale(), entry.comp_matrix);
			fvector pos(bone_matrix._41, bone_matrix._42, bone_matrix._43);
			entry.cached_pos[id] = pos;
			entry.has_pos[id] = true;
			return pos;
		}
	}

	// Fallback - uncached path
	auto bone = device_().read<FTransform>(bone_ + id * 0x70);
	D3DMATRIX bone_matrix = MatrixMultiplication(bone.ToMatrixWithScale(), comp_.ToMatrixWithScale());
	return fvector(bone_matrix._41, bone_matrix._42, bone_matrix._43);
}

std::unordered_map<int, fvector> Game_::World_::get_skeleton(const std::vector<std::pair<int, int>>& bone_pairs) {
	std::unordered_map<int, fvector> bone_positions;
	bone_positions.reserve(bone_pairs.size() * 2);

	for (const auto& pair : bone_pairs) {
		for (int id : {pair.first, pair.second}) {
			auto result = bone_positions.emplace(id, fvector{});
			if (result.second) {
				result.first->second = get_bone(id);
			}
		}
	}

	return bone_positions;
}

void Game_::Camera_::get_camera() {
	if (g_cameraCached) return;
	g_cameraCached = true;

	auto& cache = GameCache::Cache::getInstance();

	// ================================================================
	//  Camera: PRIMARY = CameraCachePrivate.POV (43.00 SDK dump)
	//    CameraCachePrivate = pcm + 0x15A0, POV = +0x10
	//    POV.Location  = pcm + 0x15B0  (fvector, 3 doubles)
	//    POV.Rotation  = pcm + 0x15C8  (frotator, 3 doubles P/Y/R, degrees)
	//    POV.FOV       = pcm + 0x15E0  (float, degrees)
	//  FALLBACK = LO's verified paste recipe (plain UWorld pointer reads)
	//    location_ptr = read(world + 0x178), rotation_ptr = read(world + 0x188)
	//    Rotation doubles: a @ +0x00, b @ +0x20, c @ +0x1D0
	//    FOV: read<float>(pc + 0x37C) * 90.0f
	//  (Old ViewState + encrypted-FNRot methods removed - dead on 43.00)
	// ================================================================
	auto pcm = cache.get_camera_manager().value_or(0);
	auto rot_ptr = cache.get_rotation_ptr().value_or(0);
	auto loc_ptr = cache.get_location_ptr().value_or(0);
	uintptr_t pc = cache.get_pcontroller().value_or(0);

	// POV.Location offset inside PlayerCameraManager. 0x15B0 per the 43.00 SDK
	// guess; the POV hunt (below) re-locates it by change-detection if dead.
	static uint32_t g_povLocOff = 0x15B0;

	bool pov_ok = false;
	if (pcm) {
		// POV is the final composed camera (after all modifiers) - plain reads,
		// no FNRot crypto. fvector/frotator are double-based, matching UE5.3.
		fvector pov_loc = device_().read<fvector>(pcm + g_povLocOff);
		frotator pov_rot = device_().read<frotator>(pcm + g_povLocOff + 0x18);
		float pov_fov = device_().read<float>(pcm + g_povLocOff + 0x30);

		// Sanity: a live POV has a real-world location and FOV in [10, 180]
		if (pov_fov >= 10.0f && pov_fov <= 180.0f &&
			(fabs(pov_loc.x) > 100.0 || fabs(pov_loc.y) > 100.0)) {
			location = pov_loc;
			rotation = pov_rot;
			fov = pov_fov;
			pov_ok = true;
			g_camSourceName = "POV (CameraCachePrivate)";
		}
	}

	if (!pov_ok) {
		// Fallback: LO's paste recipe
		g_camSourceName = "Paste (UWorld ptr)";

		if (pc) {
			fov = device_().read<float>(pc + 0x37C) * 90.0f;
		} else if (pcm) {
			fov = device_().read<float>(pcm + 0x37C) * 90.0f;
		}
		if (fov <= 0.0f || fov > 180.0f) fov = 80.0f;

		if (rot_ptr && loc_ptr) {
			// Plain reads - no decryption, per LO's paste
			rotation_s.a = device_().read<double>(rot_ptr);          // +0x00
			rotation_s.b = device_().read<double>(rot_ptr + 0x20);   // +0x20
			rotation_s.c = device_().read<double>(rot_ptr + 0x1D0);  // +0x1D0

			location = device_().read<fvector>(loc_ptr);

			double clamped = rotation_s.c;
			clamped = (clamped > 1.0) ? 1.0 : (clamped < -1.0) ? -1.0 : clamped;
			rotation.Pitch = asin(clamped) * (180.0 / M_PI);
			rotation.Yaw = atan2(rotation_s.a * -1.0, rotation_s.b) * (180.0 / M_PI);
			rotation.Roll = 0.0;
		}

		// POV HUNT v2 (self-healing): find LIVE camera data inside PCM by
		// change-detection. Scan A snapshots every location-like fvector in
		// pcm+0x0000..0x2400; scan B (~2-3s later) reports whichever MOVED.
		// A frozen lobby snapshot cannot move - only live camera data does.
		// The best mover (sane rot@+0x18 + fov@+0x30) is latched as the
		// POV.Location offset; the primary path picks it up next frame.
		static int pov_hunt_gate = 0;
		static bool g_povHuntArmed = false;
		static fvector g_povScanA[160];
		static uint32_t g_povScanOff[160];
		static int g_povScanN = 0;
		if (pcm && pov_hunt_gate <= 0) {
			pov_hunt_gate = 100; // ~2-3s of render frames between A and B scans
			if (!g_povHuntArmed) {
				g_povScanN = 0;
				for (uint32_t off = 0; off <= 0x2400 && g_povScanN < 160; off += 8) {
					fvector v = device_().read<fvector>(pcm + off);
					if (fabs(v.x) > 1000.0 && fabs(v.x) < 500000.0 &&
						fabs(v.y) > 1000.0 && fabs(v.y) < 500000.0 &&
						v.z > -1000.0 && v.z < 25000.0) {
						g_povScanOff[g_povScanN] = off;
						g_povScanA[g_povScanN] = v;
						++g_povScanN;
					}
				}
				printf("[hunt] scan A: %d location-like vectors in pcm+0x0000..0x2400 (pcm=0x%llx)\n",
					   g_povScanN, (unsigned long long)pcm);
				g_povHuntArmed = true;
			} else {
				int hits = 0;
				for (int i = 0; i < g_povScanN; ++i) {
					fvector v = device_().read<fvector>(pcm + g_povScanOff[i]);
					if (fabs(v.x - g_povScanA[i].x) < 1.0 &&
						fabs(v.y - g_povScanA[i].y) < 1.0 &&
						fabs(v.z - g_povScanA[i].z) < 1.0) continue; // didn't move
					frotator r = device_().read<frotator>(pcm + g_povScanOff[i] + 0x18);
					float f = device_().read<float>(pcm + g_povScanOff[i] + 0x30);
					const bool sane = (f >= 60.0f && f <= 150.0f) &&
						(fabs(r.Pitch) <= 91.0) && (fabs(r.Yaw) <= 181.0);
					printf("[hunt] %s off=0x%X moved (%.1f,%.1f,%.1f)->(%.1f,%.1f,%.1f) rot@+18=(P=%.2f Y=%.2f R=%.2f) fov@+30=%.2f\n",
						   sane ? "LIVE" : "move",
						   g_povScanOff[i],
						   g_povScanA[i].x, g_povScanA[i].y, g_povScanA[i].z,
						   v.x, v.y, v.z, r.Pitch, r.Yaw, r.Roll, f);
					if (sane && hits == 0) {
						g_povLocOff = g_povScanOff[i];
						printf("[hunt] *** LATCH POV loc off=0x%X (was 0x15B0) - primary retries next frame ***\n",
							   g_povLocOff);
					}
					++hits;
					if (hits >= 8) break;
				}
				if (hits == 0)
					printf("[hunt] nothing moved in pcm+0x0000..0x2400 - PCM stale/freed (re-arming)\n");
				g_povHuntArmed = false;
			}
		}
		if (pov_hunt_gate > 0) --pov_hunt_gate;
	}

	// [cam] telemetry every ~120 render frames: raw chain state for console
	// diagnosis. Shows whether the UWorld view-chain pointers carry LIVE data
	// (values change as LO looks around) or a frozen snapshot.
	static uint64_t cam_diag_frame = 0;
	if ((g_frameCounter - cam_diag_frame) >= 120) {
		cam_diag_frame = g_frameCounter;
		const double ra = rot_ptr ? device_().read<double>(rot_ptr) : 0.0;
		const double rb = rot_ptr ? device_().read<double>(rot_ptr + 0x20) : 0.0;
		const double rc = rot_ptr ? device_().read<double>(rot_ptr + 0x1D0) : 0.0;
		fvector lv{};
		if (loc_ptr) lv = device_().read<fvector>(loc_ptr);
		const float fov_raw = pc ? device_().read<float>(pc + 0x37C) : -1.0f;
		printf("[cam] pcm=%p pc=%p loc_ptr=%p rot_ptr=%p | loc=(%.1f,%.1f,%.1f) rot=(%.3f,%.3f,%.3f) fov_raw=%.3f src=%s\n",
			   (void*)pcm, (void*)pc, (void*)loc_ptr, (void*)rot_ptr,
			   lv.x, lv.y, lv.z, ra, rb, rc, fov_raw, g_camSourceName);
	}
}

auto in_screen(fvector2d screen_location) -> bool {

	if (screen_location.x > 0 && screen_location.x < C_Overlay::c_screen_width && screen_location.y > 0 && screen_location.y < C_Overlay::c_screen_height) return true;
	else return false;
}


fvector2d Game_::Camera_::w2s(fvector worldlocation) {

	camera_manager.get_camera();

	// Cache rotation matrix + fov scale once per frame
	if (!g_w2sCached) {
		_MATRIX tempMatrix = Matrix1(rotation);
		g_cachedAxisX = fvector(tempMatrix.m[0][0], tempMatrix.m[0][1], tempMatrix.m[0][2]);
		g_cachedAxisY = fvector(tempMatrix.m[1][0], tempMatrix.m[1][1], tempMatrix.m[1][2]);
		g_cachedAxisZ = fvector(tempMatrix.m[2][0], tempMatrix.m[2][1], tempMatrix.m[2][2]);
		g_cachedFovScale = (C_Overlay::c_screen_width / 2.0f) / tanf(fov * (float)M_PI / 360.f);
		g_w2sCached = true;
	}

	fvector vDelta = worldlocation - location;
	fvector vTransformed = fvector(vDelta.Dot(g_cachedAxisY), vDelta.Dot(g_cachedAxisZ), vDelta.Dot(g_cachedAxisX));

	// Object is behind the camera â€” return off-screen sentinel so nothing draws
	if (vTransformed.z < 1.f)
		return fvector2d(-1.f, -1.f);

	return fvector2d(
		(C_Overlay::c_screen_width / 2.0f) + vTransformed.x * g_cachedFovScale / vTransformed.z,
		(C_Overlay::c_screen_height / 2.0f) - vTransformed.y * g_cachedFovScale / vTransformed.z
	);
}

// ============================================================================
//  find_look_at_rotation (Popstar method)
//  Compute the rotation needed to look from 'start' toward 'target'
// ============================================================================
frotator find_look_at_rotation(fvector start, fvector target) {
	fvector direction = target - start;
	float len = sqrtf(direction.x * direction.x + direction.y * direction.y + direction.z * direction.z);
	if (len > 0.0001f) {
		direction.x /= len;
		direction.y /= len;
		direction.z /= len;
	}
	float yaw = atan2(direction.y, direction.x) * (180.0 / M_PI);
	float pitch = atan2(direction.z, sqrtf(direction.x * direction.x + direction.y * direction.y)) * (180.0 / M_PI);
	return frotator{ pitch, yaw, 0.0f };
}

// ============================================================================
//  deproject (Popstar method)
//  Convert a screen position back to a world-space ray direction
// ============================================================================
fvector deproject(fvector2d screen_position) {
	camera_manager.get_camera();

	float fov_radians = tanf(fov * (float)M_PI / 360.0f);
	fvector view_space(
		(screen_position.x - C_Overlay::c_screen_width / 2.0f) * fov_radians / (C_Overlay::c_screen_width / 2.0f),
		(C_Overlay::c_screen_height / 2.0f - screen_position.y) * fov_radians / (C_Overlay::c_screen_width / 2.0f),
		1.0f
	);

	if (!g_w2sCached) {
		_MATRIX tempMatrix = Matrix1(rotation);
		g_cachedAxisX = fvector(tempMatrix.m[0][0], tempMatrix.m[0][1], tempMatrix.m[0][2]);
		g_cachedAxisY = fvector(tempMatrix.m[1][0], tempMatrix.m[1][1], tempMatrix.m[1][2]);
		g_cachedAxisZ = fvector(tempMatrix.m[2][0], tempMatrix.m[2][1], tempMatrix.m[2][2]);
		g_cachedFovScale = (C_Overlay::c_screen_width / 2.0f) / tanf(fov * (float)M_PI / 360.f);
		g_w2sCached = true;
	}

	// Reconstruct world direction from view-space coords
	fvector world_dir(
		g_cachedAxisY.x * view_space.x + g_cachedAxisZ.x * view_space.y + g_cachedAxisX.x * view_space.z,
		g_cachedAxisY.y * view_space.x + g_cachedAxisZ.y * view_space.y + g_cachedAxisX.y * view_space.z,
		g_cachedAxisY.z * view_space.x + g_cachedAxisZ.z * view_space.y + g_cachedAxisX.z * view_space.z
	);

	// Normalize
	float dir_len = sqrtf(world_dir.x * world_dir.x + world_dir.y * world_dir.y + world_dir.z * world_dir.z);
	if (dir_len > 0.0001f) {
		world_dir.x /= dir_len;
		world_dir.y /= dir_len;
		world_dir.z /= dir_len;
	}
	return world_dir;
}
