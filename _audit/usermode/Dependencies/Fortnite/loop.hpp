#include "drawing.hpp"
#include <cstdio>
#include <iostream>
#include <vector>
#include <memory>
#include <optional>
#include <thread>
#include <chrono>
#include "../../m_world.hpp"
#include <mutex>
#include <stdexcept>
#include "../../Includes/Definitions/c_obfuscation.hpp"
using namespace std;
__int64 va_text;
vector<fvector> chestloc;
vector<fvector> ammo;
class item {
public:
    uintptr_t
        Actor;

    std::string
        Name;
    bool
        isVehicle,
        isChest,
        isPickup,
        isAmmoBox;
    float
        distance;
};
std::vector<item> item_pawns;
std::vector<entities> temporary_entity_list;

enum class EFortRarity : uint8_t
{
    EFortRarity__Common = 0,
    EFortRarity__Uncommon = 1,
    EFortRarity__Rare = 2,
    EFortRarity__Epic = 3,
    EFortRarity__Legendary = 4,
    EFortRarity__Mythic = 5,
    EFortRarity__Transcendent = 6,
    EFortRarity__Unattainable = 7,
    EFortRarity__NumRarityValues = 8,
};

ImVec4 get_loot_color(EFortRarity tier) {
    if (tier == EFortRarity::EFortRarity__Common)
        return ImVec4(123 / 255.0f, 123 / 255.0f, 123 / 255.0f, 1.f);
    else if (tier == EFortRarity::EFortRarity__Uncommon)
        return ImVec4(58 / 255.0f, 121 / 255.0f, 19 / 255.0f, 1.f);
    else if (tier == EFortRarity::EFortRarity__Rare)
        return ImVec4(18 / 255.0f, 88 / 255.0f, 162 / 255.0f, 1.f);
    else if (tier == EFortRarity::EFortRarity__Epic)
        return ImVec4(189 / 255.0f, 63 / 255.0f, 250 / 255.0f, 1.f);
    else if (tier == EFortRarity::EFortRarity__Legendary)
        return ImVec4(255 / 255.0f, 118 / 255.0f, 5 / 255.0f, 1.f);
    else if (tier == EFortRarity::EFortRarity__Mythic)
        return ImVec4(220 / 255.0f, 160 / 255.0f, 30 / 255.0f, 1.f);
    else if (tier == EFortRarity::EFortRarity__Transcendent)
        return ImVec4(0 / 255.0f, 225 / 255.0f, 252 / 255.0f, 1.f);

    return ImVec4(123 / 255.0f, 123 / 255.0f, 123 / 255.0f, 1.f);
}

std::string get_loot_tier_name(EFortRarity tier) {
    if (tier == EFortRarity::EFortRarity__Common)
        return "Common";
    else if (tier == EFortRarity::EFortRarity__Uncommon)
        return "Uncommon";
    else if (tier == EFortRarity::EFortRarity__Rare)
        return "Rare";
    else if (tier == EFortRarity::EFortRarity__Epic)
        return "Epic";
    else if (tier == EFortRarity::EFortRarity__Legendary)
        return "Legendary";
    else if (tier == EFortRarity::EFortRarity__Mythic)
        return "Mythic";
    else if (tier == EFortRarity::EFortRarity__Transcendent)
        return "Transcendent";

    return "Invalid";
}

void DrawString3(float fontSize, int x, int y, ImVec4 color, bool bCenter, bool stroke, const char* pText, ...)
{
    va_list va_alist;
    char buf[1024] = { 0 };
    va_start(va_alist, pText);
    _vsnprintf_s(buf, sizeof(buf), pText, va_alist);
    va_end(va_alist);
    std::string text = wstring_to_utf8(string_to_wstring(buf).c_str());
    if (bCenter)
    {
        ImVec2 textSize = ImGui::CalcTextSize(text.c_str());
        x = x - textSize.x / 4;
        y = y - textSize.y;
    }
    auto dl = ImGui::GetBackgroundDrawList();
    auto font = ImGui::GetFont();
    if (stroke)
    {
        dl->AddText(font, fontSize, ImVec2(x + 1, y + 1), IM_COL32(1, 1, 1, 255), text.c_str());
    }
    dl->AddText(font, fontSize, ImVec2(x, y), ImGui::ColorConvertFloat4ToU32(color), text.c_str());
}

void DrawString2(float fontSize, int x, int y, ImColor color, bool bCenter, bool stroke, const char* pText, ...)
{
    va_list va_alist;
    char buf[1024] = { 0 };
    va_start(va_alist, pText);
    _vsnprintf_s(buf, sizeof(buf), pText, va_alist);
    va_end(va_alist);
    std::string text = wstring_to_utf8(string_to_wstring(buf).c_str());
    if (bCenter)
    {
        ImVec2 textSize = ImGui::CalcTextSize(text.c_str());
        x = x - textSize.x / 4;
        y = y - textSize.y;
    }
    auto dl = ImGui::GetBackgroundDrawList();
    auto font = ImGui::GetFont();
    if (stroke)
    {
        dl->AddText(font, fontSize, ImVec2(x + 1, y + 1), IM_COL32(1, 1, 1, 255), text.c_str());
    }
    dl->AddText(font, fontSize, ImVec2(x, y), color, text.c_str());
}

// 43.00: FNamePool entries are ENCRYPTED (index cipher + per-string PRNG).
// Pool RVA: 0x1C790700, block stride 0x2750, header key 0x025E, mask 0x03FF.
// Constants + routines from LO's verified paste (43.00 published).
static constexpr uint32_t fname_index_add      = 0x02043977u;
static constexpr uint32_t fname_index_xor      = 0x68207113u;
static constexpr uint32_t fname_index_fallback = 0x6439587Bu;
static constexpr uint32_t fname_number_xor     = 0x9EFFD30Au;
static constexpr uint32_t fname_prng_mul       = 0x219Fu;
static constexpr uint32_t fname_prng_add       = 0x78C9BBu;
static constexpr uint16_t fname_header_mask    = 0x03FFu;
static constexpr uint16_t fname_header_key     = 0x025Eu;
static constexpr uint16_t fname_wide_flag      = 0x8000u;
static constexpr uint8_t  fname_cipher_add     = 0xB6u;
static constexpr uintptr_t FNAMEPOOL_OFFSET    = 0x1C790700;
static constexpr uintptr_t FNAMEPOOL_STRIDE    = 0x2750;

static uint32_t fname_decrypt_index(uint32_t encrypted)
{
	if (!encrypted)
		return 0;

	const uint32_t value =
		_byteswap_ulong((encrypted + fname_index_add) ^ fname_index_xor) + 1u;

	return value ? value : fname_index_fallback;
}

static uint32_t fname_decrypt_number(uint32_t encrypted)
{
	if (!encrypted)
		return 0xFFFFFFFFu;

	const uint32_t value =
		_byteswap_ulong((encrypted + fname_index_add) ^ fname_number_xor);

	return value != 0xFFFFFFFFu ? value : 0x7DEAFB9Cu;
}

static uint32_t fname_next_key(uint32_t state)
{
	const uint32_t mixed = state * fname_prng_mul + fname_prng_add;
	return ((state >> 7) ^ state) + ((mixed >> 12) ^ mixed);
}

static void fname_decrypt_name(const uint8_t* src, uint8_t* dst,
	uint32_t byte_length, uint32_t char_length)
{
	uint32_t key = fname_next_key(char_length);

	for (uint32_t base = 0; base < byte_length; base += 4)
	{
		const uint32_t left = byte_length - base;
		const uint32_t count = left < 4 ? left : 4;
		const uint32_t rotation = key % count;

		for (uint32_t i = 0; i < count; ++i)
		{
			const uint8_t value = static_cast<uint8_t>(
				src[base + ((i + rotation) % count)] + fname_cipher_add);

			dst[base + i] = static_cast<uint8_t>(
				value ^ static_cast<uint8_t>(key));

			key = fname_next_key(key);
		}
	}
}

// get_name: decrypt a FNamePool index to its string (LO's paste, 43.00).
// Follows number-entry suffix chains up to 2 hops ("Name_123" style).
static std::string get_name(uintptr_t base, uint32_t raw_index)
{
	uint32_t index = fname_decrypt_index(raw_index);
	uint32_t suffix = 0xFFFFFFFFu;

	for (int hop = 0; index && hop < 2; ++hop)
	{
		const auto chunk = device_().read<uintptr_t>(
			base + FNAMEPOOL_OFFSET + FNAMEPOOL_STRIDE + 8ull * (index >> 16));

		if (!chunk)
			return {};

		const auto entry = chunk + 2ull * static_cast<uint16_t>(index);
		const uint16_t header = device_().read<uint16_t>(entry);

		if (!header)
			return {};

		if ((header & fname_header_mask) == fname_header_key)
		{
			suffix = fname_decrypt_number(device_().read<uint32_t>(entry + 2));
			index = fname_decrypt_index(device_().read<uint32_t>(entry + 6));
			continue;
		}

		const uint32_t length = (header & fname_header_mask) ^ fname_header_key;
		const bool wide = (header & fname_wide_flag) != 0;
		const uint32_t bytes = wide ? length * 2 : length;

		if (!length || length > 1024)
			return {};

		uint8_t encrypted[2048] = {};
		uint8_t decrypted[2048] = {};
		device_().read1(reinterpret_cast<void*>(entry + 2), encrypted, bytes);
		fname_decrypt_name(encrypted, decrypted, bytes, length);

		if (wide)
			return {}; // UTF-16 names unused by bone resolution

		std::string result(reinterpret_cast<const char*>(decrypted), length);

		if (suffix != 0xFFFFFFFFu)
			result += "_" + std::to_string(suffix);

		return result;
	}

	return {};
}

// Compatibility wrapper: raw encrypted index -> name.
static std::string get_fname(int key)
{
	return get_name(va_text, static_cast<uint32_t>(key));
}

namespace GameCache {

    Cache& cache = Cache::getInstance();

    uintptr_t apply_offset(uintptr_t base, uintptr_t offset) {
        return base + offset;
    }

    uintptr_t reada(uintptr_t baseAddress, uintptr_t offset) {
        return read_addy(uintptr_t, baseAddress, offset);
    }

    static auto RootC(uintptr_t actor) {
        return device_().read<uintptr_t>(actor + apply_offset_().root_());
    }

    static auto GetLocation(uintptr_t actor) {
        return device_().read<fvector>(RootC(actor) + apply_offset_().relative_loc_());
    }

    template<typename T>
    optional<T> readd(uintptr_t address) {
        try {
            return device_().read<T>(address);
        }
        catch (const exception& e) {
            return nullopt;
        }
    }

    template<typename T>
    void Cache::update_cache_val(optional<T>& cachevar, uintptr_t base, uintptr_t offset) {
        if (auto value = readd<T>(apply_offset(base, offset))) {
            cachevar = move(value);
        }
    }

    // ============================================================================
    //  Cache Chain (Popstar-style)
    //  UWorld (XOR) → GameInstance → LocalPlayers[0] → PlayerController
    //    → PlayerCameraManager → AcknowledgedPawn → Mesh/PlayerState/Root
    //  GameState → PlayerArray
    //  Camera pointers from UWorld (encrypted FNRot pattern)
    // ============================================================================
    // Probe latch state (file scope so the fail-streak re-arm can reset them).
    // One-shot probes that fire during a load screen latch dead and the chain
    // stays NULL forever — the "ESP only wakes after opening a file" symptom.
    static int  gv_offset = 0xB70;   // cached discovered GameViewport offset
    static bool gv_probed = false;
    static int  vw_offset = 0x78;    // cached discovered ViewportWorld offset
    static bool vw_probed = false;
    static bool hunt_probed = false;
    static int  chain_fail_streak = 0;
    void Cache::cache() {
        lock_guard<mutex> lock(cacheMutex_);

        // Step 1: UWorld - GEngine chain primary (SnowSable method), decrypt fallback.
        // Chain:   base + GEngine(0x1C8E2F48) -> UEngine* -> +0xB70 viewport -> +0x78 world
        // Decrypt: byteswap(encrypted) ^ 0x300B471EB37866BB  (solved from live samples)
        uworld_ = 0ULL; // engaged-0, never nullopt: downstream .value() must not throw
        uint64_t encrypted = 0ULL;
        static uintptr_t ge_fixed = 0; // hunt probe override: slot may hold stale/freed ptr
        uintptr_t gengine = ge_fixed ? ge_fixed
            : device_().read<uintptr_t>(va_text + apply_offset_().gengine_());
        uintptr_t viewport = 0ULL;
        if (gengine) {
            viewport = device_().read<uintptr_t>(gengine + gv_offset);
            if (viewport) {
                uworld_ = device_().read<uintptr_t>(viewport + apply_offset_().viewport_world_());
                // Self-healing: ViewportWorld offset can shift between sessions.
                // Sweep viewport+0x60..0x90, validate candidates by decoding their
                // class name through the proven FName crypto, auto-lock the offset.
                if (vw_offset > 0) {
                    uworld_ = device_().read<uintptr_t>(viewport + vw_offset);
                }
                if (!uworld_.value_or(0)) {
                    if (!vw_probed) {
                        vw_probed = true;
                        printf("[probe] viewport=%p dumping +0x60..0x90\n", (void*)viewport);
                        for (int off = 0x60; off <= 0x90; off += 8) {
                            const uintptr_t val = device_().read<uintptr_t>(viewport + off);
                            std::string cls;
                            if (val) {
                                const uintptr_t ucl = device_().read<uintptr_t>(val + 0x20);
                                if (ucl) cls = get_name(va_text, device_().read<uint32_t>(ucl + 0x8));
                            }
                            printf("[probe]   +0x%02x = %p %s\n", off, (void*)val, cls.c_str());
                        }
                    }
                    for (int off = 0x60; off <= 0x90; off += 8) {
                        const uintptr_t cand = device_().read<uintptr_t>(viewport + off);
                        if (!cand) continue;
                        const uintptr_t ucl = device_().read<uintptr_t>(cand + 0x20);
                        if (!ucl) continue;
                        const std::string cls = get_name(va_text, device_().read<uint32_t>(ucl + 0x8));
                        if (cls.find("World") == std::string::npos) continue;
                        vw_offset = off;
                        uworld_ = cand;
                        printf("[scan] ViewportWorld relocated: +0x%02x -> %p (%s)\n",
                            off, (void*)cand, cls.c_str());
                        break;
                    }
                }
            } else if (!gv_probed) {
                // Self-healing: GameViewport offset can shift between builds.
                // One-shot: validate gengine's class via FName crypto, then sweep
                // the UEngine layout for a pointer whose class contains "Viewport".
                gv_probed = true;
                std::string eng_cls;
                {
                    const uintptr_t ucl = device_().read<uintptr_t>(gengine + 0x20);
                    if (ucl) eng_cls = get_name(va_text, device_().read<uint32_t>(ucl + 0x8));
                }
                printf("[gprobe] gengine=%p class=%s\n", (void*)gengine, eng_cls.c_str());
                if (eng_cls.find("Engine") == std::string::npos) {
                    printf("[gprobe] class mismatch! dumping GEngine slot neighborhood\n");
                    for (int off = -0x20; off <= 0x20; off += 8) {
                        const uintptr_t val = device_().read<uintptr_t>(
                            va_text + apply_offset_().gengine_() + off);
                        printf("[gprobe]   gslot %+d = %p\n", off, (void*)val);
                    }
                }
                int found = 0;
                for (int off = 0xA00; off <= 0xD00; off += 8) {
                    const uintptr_t val = device_().read<uintptr_t>(gengine + off);
                    if (!val) continue;
                    const uintptr_t ucl = device_().read<uintptr_t>(val + 0x20);
                    if (!ucl) continue;
                    const std::string cls = get_name(va_text, device_().read<uint32_t>(ucl + 0x8));
                    printf("[gprobe]   +0x%03x = %p %s\n", off, (void*)val, cls.c_str());
                    if (found == 0 && cls.find("Viewport") != std::string::npos) found = off;
                }
                if (found) {
                    gv_offset = found;
                    viewport = device_().read<uintptr_t>(gengine + gv_offset);
                    if (viewport) uworld_ = device_().read<uintptr_t>(viewport + apply_offset_().viewport_world_());
                    printf("[gscan] GameViewport relocated: +0x%03x -> %p\n",
                        found, (void*)viewport);
                } else {
                    printf("[gscan] no Viewport-classed pointer in +0xA00..0xD00\n");
                }
            }
            // Hunt probe (re-armable): live-test GWorld decrypt, chase heap pointers,
            // sweep .data for the real GEngine, auto-repoint and resolve the chain.
            if (!hunt_probed) {
                hunt_probed = true;
                printf("[hunt] === config ===\n");
                printf("[hunt] gengine_off=0x%llx uworld_off=0x%llx gv=0x%x vw=0x%x\n",
                    (unsigned long long)apply_offset_().gengine_(),
                    (unsigned long long)apply_offset_().uworld_(),
                    (unsigned)gv_offset, (unsigned)0x78);
                // 1) live GWorld decrypt test
                {
                    const uint64_t enc = device_().read<uint64_t>(va_text + apply_offset_().uworld_());
                    printf("[hunt] encrypted@GWorld = %016llx\n", (unsigned long long)enc);
                    if (enc) {
                        const uintptr_t cand = _byteswap_uint64(enc) ^ 0x300B471EB37866BBull;
                        printf("[hunt] decrypt candidate = %p\n", (void*)cand);
                        if (cand > 0x10000 && cand < 0x7FFFFFFEFFFFull) {
                            const uintptr_t ucl = device_().read<uintptr_t>(cand + 0x20);
                            std::string cls;
                            if (ucl) cls = get_name(va_text, device_().read<uint32_t>(ucl + 0x8));
                            printf("[hunt]   cand class=%s\n", cls.c_str());
                            if (cls.find("World") != std::string::npos) {
                                uworld_ = cand;
                                printf("[hunt] *** decrypt VALID -> uworld=%p ***\n", (void*)cand);
                            }
                        } else {
                            printf("[hunt]   candidate non-canonical, formula/offset wrong\n");
                        }
                    }
                }
                // 2) heap chase: follow .data pointers, read targets (heap-read health)
                printf("[hunt] === heap chase ===\n");
                {
                    const long long gos[] = { -64, -56, 120, 128 };
                    for (int i = 0; i < 4; ++i) {
                        const uintptr_t p = device_().read<uintptr_t>(
                            va_text + apply_offset_().gengine_() + gos[i]);
                        if (p < 0x10000 || p > 0x7FFFFFFEFFFFull) continue;
                        const uintptr_t vt = device_().read<uintptr_t>(p);
                        const uintptr_t ucl = device_().read<uintptr_t>(p + 0x20);
                        std::string cls;
                        if (ucl) cls = get_name(va_text, device_().read<uint32_t>(ucl + 0x8));
                        printf("[hunt] ge%+lld -> %p vt=%p cls=%s\n",
                            gos[i], (void*)p, (void*)vt, cls.c_str());
                    }
                    const long long gws[] = { -48, -24, 48, 72, 96, 128, 160 };
                    for (int i = 0; i < 7; ++i) {
                        const uintptr_t p = device_().read<uintptr_t>(
                            va_text + apply_offset_().uworld_() + gws[i]);
                        if (p < 0x10000 || p > 0x7FFFFFFEFFFFull) continue;
                        const uintptr_t vt = device_().read<uintptr_t>(p);
                        const uintptr_t ucl = device_().read<uintptr_t>(p + 0x20);
                        std::string cls;
                        if (ucl) cls = get_name(va_text, device_().read<uint32_t>(ucl + 0x8));
                        printf("[hunt] gw%+lld -> %p vt=%p cls=%s\n",
                            gws[i], (void*)p, (void*)vt, cls.c_str());
                    }
                }
                // 3) FName pool chunk reads (heap-allocated)
                {
                    const uint32_t idxs[] = { 0x00000000u, 0x00010000u, 0x00020000u, 0x00030000u };
                    for (int i = 0; i < 4; ++i) {
                        const uintptr_t chunk = device_().read<uintptr_t>(
                            va_text + FNAMEPOOL_OFFSET + FNAMEPOOL_STRIDE + 8ull * (idxs[i] >> 16));
                        printf("[hunt] fname chunk[%u] = %p\n", idxs[i], (void*)chunk);
                    }
                }
                // 4) GEngine hunt: sweep .data for heap-ptr whose class contains Engine
                printf("[hunt] === gengine sweep ===\n");
                uintptr_t new_ge = 0;
                for (uintptr_t da = va_text + 0x1C8E2800; da < va_text + 0x1C8E3600; da += 8) {
                    const uintptr_t v = device_().read<uintptr_t>(da);
                    if (v < 0x10000 || v > 0x7FFFFFFEFFFFull) continue;
                    const uintptr_t ucl = device_().read<uintptr_t>(v + 0x20);
                    if (!ucl || ucl < 0x10000 || ucl > 0x7FFFFFFEFFFFull) continue;
                    const std::string cls = get_name(va_text, device_().read<uint32_t>(ucl + 0x8));
                    if (cls.empty()) continue;
                    const long long d = (long long)da - (long long)(va_text + apply_offset_().gengine_());
                    printf("[hunt] cand %+lld = %p cls=%s\n", d, (void*)v, cls.c_str());
                    if (!new_ge && cls.find("Engine") != std::string::npos) new_ge = v;
                }
                if (new_ge && new_ge != gengine) {
                    printf("[hunt] *** repoint gengine %p -> %p ***\n", (void*)gengine, (void*)new_ge);
                    ge_fixed = new_ge;
                    gengine = new_ge;
                    gv_probed = false;
                    gv_offset = 0xB70;
                } else if (new_ge) {
                    printf("[hunt] slot gengine already correct\n");
                } else {
                    printf("[hunt] NO Engine-classed object in sweep\n");
                }
                // 5) inline viewport resolve on (possibly new) gengine
                if (gengine) {
                    for (int off = 0xA00; off <= 0xD00; off += 8) {
                        const uintptr_t val = device_().read<uintptr_t>(gengine + off);
                        if (!val || val < 0x10000 || val > 0x7FFFFFFEFFFFull) continue;
                        const uintptr_t ucl = device_().read<uintptr_t>(val + 0x20);
                        if (!ucl) continue;
                        const std::string cls = get_name(va_text, device_().read<uint32_t>(ucl + 0x8));
                        if (cls.find("Viewport") == std::string::npos) continue;
                        const uintptr_t uw = device_().read<uintptr_t>(val + apply_offset_().viewport_world_());
                        printf("[hunt] viewport +0x%03x = %p (%s) world=%p\n",
                            off, (void*)val, cls.c_str(), (void*)uw);
                        if (uw && uw > 0x10000 && uw < 0x7FFFFFFEFFFFull) {
                            const uintptr_t ucl2 = device_().read<uintptr_t>(uw + 0x20);
                            std::string wcls;
                            if (ucl2) wcls = get_name(va_text, device_().read<uint32_t>(ucl2 + 0x8));
                            printf("[hunt]   world class=%s\n", wcls.c_str());
                            if (wcls.find("World") != std::string::npos) {
                                uworld_ = uw;
                                gv_offset = off;
                                printf("[hunt] *** CHAIN RESOLVED uworld=%p ***\n", (void*)uw);
                            }
                        }
                        break;
                    }
                }
                printf("[hunt] === done ===\n");
            }
        }

        // Fallback: published 43.00 decrypt (GEngine global is zeroed on some patches)
        // Range-check the candidate: encrypted=0 decrypts to the raw key (non-zero
        // garbage) — accepting it poisons the chain AND pins the fail streak at
        // zero, so the probe re-arm would never fire.
        if (!uworld_) {
            encrypted = device_().read<uint64_t>(va_text + apply_offset_().uworld_());
            if (encrypted) {
                const uintptr_t cand = _byteswap_uint64(encrypted) ^ 0x300B471EB37866BBull;
                if (cand > 0x10000 && cand < 0x7FFFFFFEFFFFull) {
                    uworld_ = cand;
                }
            }
        }

        // [diag] trace - every pass until UWorld resolves (300 passes), then a
        // slow heartbeat every 50th pass so a wedged loop is never silent.
        static int diag_passes = 0;
        ++diag_passes;
        if (diag_passes <= 300 || (diag_passes % 50) == 0) {
            const uint16_t mz = device_().read<uint16_t>(va_text); // PE DOS sig, expect 5a4d
            printf("[diag] pass %d | mz=%04x va_text=%p gengine=%p viewport=%p encrypted=%llx uworld=%p\n",
                diag_passes,
                (unsigned)mz,
                (void*)(uintptr_t)va_text,
                (void*)(uintptr_t)gengine,
                (void*)(uintptr_t)viewport,
                (unsigned long long)encrypted,
                (void*)(uintptr_t)uworld_.value_or(0));
        }

        if (!uworld_ || !uworld_.value()) {
            // Sustained chain failure -> re-arm the one-shot probes so they
            // re-fire (a launch during a load screen latched them dead).
            if (++chain_fail_streak >= 30) {
                chain_fail_streak = 0;
                gv_probed = false;
                vw_probed = false;
                hunt_probed = false;
                printf("[rearm] chain dead 30 passes - re-arming one-shot probes\n");
            }
            return;
        }
        chain_fail_streak = 0;

        auto uw = uworld_.value();

        // Step 2: UWorld → GameInstance, GameState, Camera pointers
        gameInstance_ = device_().read<uintptr_t>(uw + apply_offset_().game_instance_());
        gameState_ = device_().read<uintptr_t>(uw + apply_offset_().game_state_());
        locationPtr_ = device_().read<uintptr_t>(uw + apply_offset_().location_ptr_());
        rotationPtr_ = device_().read<uintptr_t>(uw + apply_offset_().rotation_ptr_());

        // Step 3: GameState → PlayerArray
        auto gs = gameState_.value_or(0);
        if (gs) {
            playerArray_ = device_().read<uintptr_t>(gs + apply_offset_().player_array_());
            playerArraySize_ = device_().read<int>(gs + apply_offset_().player_array_() + sizeof(uintptr_t));
        }

        // Step 4: GameInstance → LocalPlayers[0] (double deref TArray)
        auto gi = gameInstance_.value_or(0);
        if (gi) {
            uintptr_t lp_data = device_().read<uintptr_t>(gi + apply_offset_().local_player_());
            localPlayer_ = lp_data ? device_().read<uintptr_t>(lp_data) : 0ULL;
        }

        // Step 5: LocalPlayer → PlayerController
        auto lp = localPlayer_.value_or(0);
        if (lp) {
            playerController_ = device_().read<uintptr_t>(lp + apply_offset_().player_controller_());
        } else {
            playerController_ = 0ULL;
        }

        // Step 6: PlayerController → PlayerCameraManager + AcknowledgedPawn
        auto pc = playerController_.value_or(0);
        if (pc) {
            playerCameraManager_ = device_().read<uintptr_t>(pc + apply_offset_().player_camera_manager_());
            acknowledgedPawn_ = device_().read<uintptr_t>(pc + apply_offset_().local_pawn_());
        } else {
            playerCameraManager_ = 0ULL;
            acknowledgedPawn_ = 0ULL;
        }

        // Step 7: AcknowledgedPawn → Mesh, PlayerState, RootComponent
        auto pawn = acknowledgedPawn_.value_or(0);
        if (pawn) {
            skeletalMesh_ = device_().read<uintptr_t>(pawn + apply_offset_().mesh_());
            playerState_ = device_().read<uintptr_t>(pawn + apply_offset_().pstate_());
            rootComponent_ = device_().read<uintptr_t>(pawn + apply_offset_().root_());
        } else {
            skeletalMesh_ = 0ULL;
            playerState_ = 0ULL;
            rootComponent_ = 0ULL;
        }

        // Step 8: TeamIndex from PlayerState
        auto ps = playerState_.value_or(0);
        teamIndex_ = ps ? device_().read<char>(ps + apply_offset_().team_id_()) : (char)0;

        // [diag] FName smoke test - PC -> ClassPrivate -> NamePrivate -> get_name.
        // Throttled to every 20th pass; proves the 43.00 FName crypto end-to-end
        // (pool walk + PRNG decrypt) before ESP relies on it.
        static int fname_pass = 0;
        if (pc && (++fname_pass % 20) == 0) {
            const uintptr_t uclass = device_().read<uintptr_t>(pc + 0x20); // ClassPrivate
            if (uclass) {
                const uint32_t name_idx = device_().read<uint32_t>(uclass + 0x8); // NamePrivate
                const std::string cls = get_name(va_text, name_idx);
                printf("[diag] fname | pc_class=%p idx=%u name='%s'\n",
                    (void*)(uintptr_t)uclass, name_idx, cls.c_str());
            }
        }
    }

    // ============================================================================
    //  Entity Population (Popstar-style double-buffered)
    //  Iterates GameState → PlayerArray, reads PawnPrivate → Mesh for each
    //  Filters by team and skips self
    // ============================================================================
    void cachep() {
        Cache& cache = Cache::getInstance();
        cache.cache();

        auto& back = g_entityBuffer.back();
        back.clear();

        auto pa = cache.get_playerarray().value_or(0);
        auto count = cache.get_playersize().value_or(0);
        auto local_pawn = cache.get_local_pawn().value_or(0);
        auto local_team = cache.get_teamid().value_or(0);

        // [ent] diagnostic: first 8 PlayerArray entries ground truth, reprinted
        // whenever the array size changes (lobby -> match transition).
        // Discriminates: stale PawnPrivate (pawn=0 for all), stale Mesh (mesh=0),
        // stale PlayerArray (pstate=0/wild), team filter eating everyone.
        static int ent_diag_last_count = -1;
        if (pa && count > 0 && count != ent_diag_last_count) {
            ent_diag_last_count = count;
            printf("[ent] pa=%p count=%d local_pawn=%p local_team=%d\n",
                (void*)pa, count, (void*)local_pawn, (int)local_team);
            const int n = count < 8 ? count : 8;
            for (int i = 0; i < n; ++i) {
                const uintptr_t pst = reada(pa, i * sizeof(uintptr_t));
                if (!pst) { printf("[ent] [%d] pstate=0\n", i); continue; }
                const uintptr_t pn = reada(pst, apply_offset_().pawn_private_());
                const uintptr_t mh = pn ? reada(pn, apply_offset_().mesh_()) : 0;
                const int tm = (int)device_().read<char>(pst + apply_offset_().team_id_());
                printf("[ent] [%d] pstate=%p pawn=%p mesh=%p team=%d\n",
                    i, (void*)pst, (void*)pn, (void*)mh, tm);
            }
        }

        if (!pa || count <= 0) {
            g_entityBuffer.swap();
            sleep_for(milliseconds(100)); // fast re-poll: lobbies/respawns drop in fast
            return;
        }

        int io_reads = 0;
        for (int i = 0; i < count; ++i) {
            if (C_Exploits::c_thread_throttle) {
                obf::throttle_io_burst(++io_reads, 30);
            }

            // PlayerArray[i] → PlayerState
            uintptr_t pstate = reada(pa, i * sizeof(uintptr_t));
            if (!pstate) continue;

            // PlayerState → PawnPrivate (the actual character pawn)
            uintptr_t pawn = reada(pstate, apply_offset_().pawn_private_());
            // Lobby players have no PawnPrivate yet — keep them so ESP populates in lobby.
            if (!pawn) {
                entities lobby_ent;
                lobby_ent.entity_ = 0;
                lobby_ent.mesh_ = 0;
                lobby_ent.root_ = 0;
                lobby_ent.pstate_ = pstate;
                lobby_ent.local_player_state_ = 0;
                back.push_back(lobby_ent);
                continue;
            }

            // Skip self
            if (local_pawn && pawn == local_pawn) continue;

            // Pawn → Mesh
            uintptr_t mesh = reada(pawn, apply_offset_().mesh_());
            if (pawn && !mesh) continue;

            // Team filter (only when we have local pawn/team info)
            if (local_pawn) {
                int team = (int)device_().read<char>(pstate + apply_offset_().team_id_());
                if (team == local_team) continue;
            }

            // Get the actor's PlayerState for additional info (name, rank, etc.)
            uintptr_t actor_pstate = pawn ? reada(pawn, apply_offset_().pstate_()) : 0;

            entities ent;
            ent.entity_ = pawn;
            ent.mesh_ = mesh;
            ent.pstate_ = pstate;
            ent.local_player_state_ = actor_pstate;
            back.push_back(ent);
        }

        g_entityBuffer.swap();

        DWORD delay = C_Exploits::c_thread_throttle
            ? obf::adaptive_cache_delay(static_cast<int>(back.size()))
            : 80; // 1ms timer res: 80ms keeps ESP tracking tight without I/O spam
        sleep_for(milliseconds(delay));
    }
}
