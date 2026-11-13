#pragma once
#include <cstdint>
#include <windows.h>

// ============================================================================
//  Control-Flow Obfuscation Toolkit
//
//  Techniques from Control-Flow-Obfuscator research:
//  - Compile-time FNV-1a string hashing (avoid plaintext in binary)
//  - Indirect call dispatcher (hides call targets from static analysis)
//  - PE header zeroing (anti-dump protection)
//  - Thread priority management (reduce CPU footprint)
//  - Fake PE timestamp / signature spoofing
// ============================================================================

// ============================================================================
//  1. Compile-Time FNV-1a Hash
//  Usage: constexpr auto h = CT_HASH("MyString");
//  Avoids storing plaintext strings in the binary for comparison
// ============================================================================
namespace obf {

    constexpr uint32_t FNV_OFFSET = 0x811C9DC5;
    constexpr uint32_t FNV_PRIME  = 0x01000193;

    constexpr uint32_t fnv1a_hash(const char* str, uint32_t hash = FNV_OFFSET) {
        return (*str == '\0') ? hash : fnv1a_hash(str + 1, (hash ^ static_cast<uint32_t>(*str)) * FNV_PRIME);
    }

    // Runtime version for dynamic strings
    inline uint32_t fnv1a_rt(const char* str) {
        uint32_t hash = FNV_OFFSET;
        while (*str) {
            hash ^= static_cast<uint32_t>(*str++);
            hash *= FNV_PRIME;
        }
        return hash;
    }
}

#define CT_HASH(str) (obf::fnv1a_hash(str))
#define RT_HASH(str) (obf::fnv1a_rt(str))

// ============================================================================
//  2. Indirect Call Dispatcher
//  Calls function through a volatile pointer to prevent inlining/devirt
//  Defeats static analysis that traces direct call graphs
// ============================================================================
namespace obf {

    template<typename Func>
    __forceinline auto indirect_call(Func fn) {
        volatile auto ptr = fn;
        return ptr;
    }

    // Indirect with argument forwarding
    template<typename Ret, typename... Args>
    __forceinline Ret call_indirect(Ret(*fn)(Args...), Args... args) {
        volatile auto ptr = fn;
        return ptr(args...);
    }
}

// CALL_INDIRECT(func, args...) - route function call through volatile pointer
#define CALL_INDIRECT(fn, ...) (obf::indirect_call(fn))(__VA_ARGS__)

// ============================================================================
//  3. PE Header Zeroing (Anti-Dump)
//  Zeros the DOS header and PE signature of our module in memory
//  Prevents memory dumpers from reconstructing valid PE
// ============================================================================
namespace obf {

    inline void zero_pe_header() {
        HMODULE hMod = GetModuleHandleA(nullptr);
        if (!hMod) return;

        DWORD old_protect;
        // Zero DOS header + PE header (first 4KB page)
        if (VirtualProtect(hMod, 0x1000, PAGE_READWRITE, &old_protect)) {
            // Zero MZ signature
            PIMAGE_DOS_HEADER dos = reinterpret_cast<PIMAGE_DOS_HEADER>(hMod);
            LONG pe_offset = dos->e_lfanew;

            // Zero DOS header fields
            SecureZeroMemory(hMod, sizeof(IMAGE_DOS_HEADER));

            // Zero PE signature + file header + optional header
            if (pe_offset > 0 && pe_offset < 0x1000) {
                BYTE* pe_start = reinterpret_cast<BYTE*>(hMod) + pe_offset;
                size_t pe_size = sizeof(IMAGE_NT_HEADERS);
                if (pe_offset + pe_size <= 0x1000) {
                    SecureZeroMemory(pe_start, pe_size);
                }
            }

            VirtualProtect(hMod, 0x1000, old_protect, &old_protect);
        }
    }

    // Spoof PE timestamp to a random date (makes binary look different each run)
    inline void spoof_pe_timestamp() {
        HMODULE hMod = GetModuleHandleA(nullptr);
        if (!hMod) return;

        PIMAGE_DOS_HEADER dos = reinterpret_cast<PIMAGE_DOS_HEADER>(hMod);
        if (dos->e_magic != IMAGE_DOS_SIGNATURE) return;

        PIMAGE_NT_HEADERS nt = reinterpret_cast<PIMAGE_NT_HEADERS>(
            reinterpret_cast<BYTE*>(hMod) + dos->e_lfanew
        );
        if (nt->Signature != IMAGE_NT_SIGNATURE) return;

        DWORD old_protect;
        if (VirtualProtect(&nt->FileHeader.TimeDateStamp, sizeof(DWORD), PAGE_READWRITE, &old_protect)) {
            // Set to a random timestamp between 2019-2024
            nt->FileHeader.TimeDateStamp = 1546300800 + (GetTickCount() % (5 * 365 * 86400));
            VirtualProtect(&nt->FileHeader.TimeDateStamp, sizeof(DWORD), old_protect, &old_protect);
        }
    }
}

// ============================================================================
//  4. Thread Priority Throttling
//  Set thread priorities to reduce CPU footprint and avoid detection
//  Cache thread: BELOW_NORMAL (background I/O)
//  Render thread: NORMAL (must maintain framerate)
// ============================================================================
namespace obf {

    inline void set_thread_stealth(HANDLE hThread, int priority = THREAD_PRIORITY_BELOW_NORMAL) {
        SetThreadPriority(hThread, priority);
    }

    // Adaptive sleep: sleep longer when few entities, shorter when active.
    // Tuned for 1ms timer resolution (timeBeginPeriod(1) in main) — the old
    // 500/300/200/150 values were padded to absorb 15.6ms scheduler slop.
    inline DWORD adaptive_cache_delay(int entity_count) {
        if (entity_count == 0) return 250;    // No players: 250ms (idle)
        if (entity_count < 5)  return 120;    // Few players: 120ms
        if (entity_count < 15) return 80;     // Normal: 80ms
        return 50;                            // Many players: 50ms (responsive)
    }

    // I/O throttle: introduces micro-delay between burst driver reads
    // Prevents CPU spike patterns that AC can fingerprint
    inline void throttle_io_burst(int reads_this_frame, int threshold = 30) {
        if (reads_this_frame > 0 && (reads_this_frame % threshold) == 0) {
            SleepEx(0, TRUE);  // Yield timeslice without hard sleep
        }
    }
}

// ============================================================================
//  5. Control Flow Flattening Helper
//  Transforms sequential code into a switch-based state machine
//  Makes control flow graph opaque to decompilers
// ============================================================================
#define CFG_FLATTEN_BEGIN(name) \
    { \
        volatile int __cfg_state_##name = 0; \
        volatile bool __cfg_running_##name = true; \
        while (__cfg_running_##name) { \
            switch (__cfg_state_##name) {

#define CFG_STATE(name, n) \
            case n:

#define CFG_NEXT(name, n) \
                __cfg_state_##name = n; \
                break;

#define CFG_FLATTEN_END(name) \
            default: \
                __cfg_running_##name = false; \
                break; \
            } \
        } \
    }

// ============================================================================
//  6. Anti-Debug Timing Check
//  Uses RDTSC to detect single-stepping in debuggers
// ============================================================================
namespace obf {

    inline bool timing_check() {
        LARGE_INTEGER freq, t1, t2;
        QueryPerformanceFrequency(&freq);
        QueryPerformanceCounter(&t1);

        // Small work unit - should take < 1ms
        volatile int dummy = 0;
        for (int i = 0; i < 100; i++) dummy += i;

        QueryPerformanceCounter(&t2);

        double elapsed_ms = (double)(t2.QuadPart - t1.QuadPart) / freq.QuadPart * 1000.0;
        // If this simple loop takes > 50ms, likely being debugged
        return elapsed_ms > 50.0;
    }
}

// ============================================================================
//  7. Import Hiding via GetProcAddress Hash
//  Resolves API functions by hash instead of name string
// ============================================================================
namespace obf {

    inline FARPROC get_proc_by_hash(HMODULE hModule, uint32_t target_hash) {
        if (!hModule) return nullptr;

        PIMAGE_DOS_HEADER dos = reinterpret_cast<PIMAGE_DOS_HEADER>(hModule);
        if (dos->e_magic != IMAGE_DOS_SIGNATURE) return nullptr;

        PIMAGE_NT_HEADERS nt = reinterpret_cast<PIMAGE_NT_HEADERS>(
            reinterpret_cast<BYTE*>(hModule) + dos->e_lfanew
        );

        auto& exportDir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
        if (!exportDir.VirtualAddress) return nullptr;

        PIMAGE_EXPORT_DIRECTORY exports = reinterpret_cast<PIMAGE_EXPORT_DIRECTORY>(
            reinterpret_cast<BYTE*>(hModule) + exportDir.VirtualAddress
        );

        DWORD* names = reinterpret_cast<DWORD*>(
            reinterpret_cast<BYTE*>(hModule) + exports->AddressOfNames
        );
        WORD* ordinals = reinterpret_cast<WORD*>(
            reinterpret_cast<BYTE*>(hModule) + exports->AddressOfNameOrdinals
        );
        DWORD* functions = reinterpret_cast<DWORD*>(
            reinterpret_cast<BYTE*>(hModule) + exports->AddressOfFunctions
        );

        for (DWORD i = 0; i < exports->NumberOfNames; i++) {
            const char* name = reinterpret_cast<const char*>(
                reinterpret_cast<BYTE*>(hModule) + names[i]
            );
            if (fnv1a_rt(name) == target_hash) {
                return reinterpret_cast<FARPROC>(
                    reinterpret_cast<BYTE*>(hModule) + functions[ordinals[i]]
                );
            }
        }
        return nullptr;
    }
}

// RESOLVE_API(module, "FunctionName") - resolve by hash at compile time
#define RESOLVE_API(hMod, name) \
    reinterpret_cast<decltype(&name)>(obf::get_proc_by_hash(hMod, CT_HASH(#name)))
