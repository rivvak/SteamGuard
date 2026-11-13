#include <ntifs.h>
#include <windef.h>
#include "cr3.h"
#include "paging.h"
#include <cstdint>
#include <stdint.h>

#define PFN_TO_PAGE(pfn) ( pfn << 12 )
#define dereference(ptr) (const uintptr_t)(ptr + *( int * )( ( BYTE * )ptr + 3 ) + 7)
#define in_range(x,a,b)    (x >= a && x <= b)
#define get_bits( x )    (in_range((x&(~0x20)),'A','F') ? ((x&(~0x20)) - 'A' + 0xA) : (in_range(x,'0','9') ? x - '0' : 0))
#define get_byte( x )    (get_bits(x[0]) << 4 | get_bits(x[1]))
#define size_align(Size) ((Size + 0xFFF) & 0xFFFFFFFFFFFFF000)
#define to_lower_i(Char) ((Char >= 'A' && Char <= 'Z') ? (Char + 32) : Char)
#define to_lower_c(Char) ((Char >= (char*)'A' && Char <= (char*)'Z') ? (Char + 32) : Char)

// PFN database structures (cr3-specific)
typedef struct _MIPFNBLINK
{
	union
	{
		struct
		{
			unsigned __int64 Blink : 40;
			unsigned __int64 NodeBlinkLow : 19;
			unsigned __int64 TbFlushStamp : 3;
			unsigned __int64 PageBlinkDeleteBit : 1;
			unsigned __int64 PageBlinkLockBit : 1;
		};
		struct
		{
			unsigned __int64 ShareCount : 62;
			unsigned __int64 PageShareCountDeleteBit : 1;
			unsigned __int64 PageShareCountLockBit : 1;
		};
		unsigned __int64 EntireField;
		volatile __int64 Lock;
		struct
		{
			unsigned __int64 LockNotUsed : 62;
			unsigned __int64 DeleteBit : 1;
			unsigned __int64 LockBit : 1;
		};
	};
} MIPFNBLINK, * PMIPFNBLINK;

typedef struct _MMPFNENTRY1
{
	struct
	{
		unsigned char PageLocation : 3;
		unsigned char WriteInProgress : 1;
		unsigned char Modified : 1;
		unsigned char ReadInProgress : 1;
		unsigned char CacheAttribute : 2;
	};
} MMPFNENTRY1, * PMMPFNENTRY1;

typedef struct _MMPFNENTRY3
{
	struct
	{
		unsigned char Priority : 3;
		unsigned char OnProtectedStandby : 1;
		unsigned char InPageError : 1;
		unsigned char SystemChargedPage : 1;
		unsigned char RemovalRequested : 1;
		unsigned char ParityError : 1;
	};
} MMPFNENTRY3, * PMMPFNENTRY3;

typedef struct _MI_PFN_ULONG5
{
	union
	{
		unsigned long EntireField;
		struct
		{
			struct
			{
				unsigned long NodeBlinkHigh : 21;
				unsigned long NodeFlinkMiddle : 11;
			};
		} StandbyList;
		struct
		{
			unsigned char ModifiedListBucketIndex : 4;
		} MappedPageList;
		struct
		{
			struct
			{
				unsigned char AnchorLargePageSize : 2;
				unsigned char Spare1 : 6;
			};
			unsigned char ViewCount;
			unsigned short Spare2;
		} Active;
	};
} MI_PFN_ULONG5, * PMI_PFN_ULONG5;

typedef struct _MMPFN
{
	union
	{
		struct _LIST_ENTRY ListEntry;
		struct _RTL_BALANCED_NODE TreeNode;
		struct
		{
			union
			{
				union
				{
					struct _SINGLE_LIST_ENTRY NextSlistPfn;
					void* Next;
					struct
					{
						unsigned __int64 Flink : 40;
						unsigned __int64 NodeFlinkLow : 24;
					};
					struct _MI_ACTIVE_PFN Active;
				};
			} u1;
			union
			{
				struct _MMPTE* PteAddress;
				unsigned __int64 PteLong;
			};
			struct _MMPTE OriginalPte;
		};
	};
	struct _MIPFNBLINK u2;
	union
	{
		union
		{
			struct
			{
				unsigned short ReferenceCount;
				struct _MMPFNENTRY1 e1;
				struct _MMPFNENTRY3 e3;
			};
			struct
			{
				unsigned short ReferenceCount;
			} e2;
			struct
			{
				unsigned long EntireField;
			} e4;
		};
	} u3;
	struct _MI_PFN_ULONG5 u5;
	union
	{
		union
		{
			struct
			{
				unsigned __int64 PteFrame : 40;
				unsigned __int64 ResidentPage : 1;
				unsigned __int64 Unused1 : 1;
				unsigned __int64 Unused2 : 1;
				unsigned __int64 Partition : 10;
				unsigned __int64 FileOnly : 1;
				unsigned __int64 PfnExists : 1;
				unsigned __int64 NodeFlinkHigh : 5;
				unsigned __int64 PageIdentity : 3;
				unsigned __int64 PrototypePte : 1;
			};
			unsigned __int64 EntireField;
		};
	} u4;
} MMPFN, * PMMPFN;

#define win_1803 17134
#define win_1809 17763
#define win_1903 18362
#define win_1909 18363
#define win_2004 19041
#define win_20H2 19569
#define win_21H1 20180

auto ReadPhysicalAddress(
	std::uintptr_t address,
	PVOID buffer,
	size_t size,
	size_t* bytes) -> NTSTATUS
{
	MM_COPY_ADDRESS target_address = { 0 };
	target_address.PhysicalAddress.QuadPart = address;
	return spuf(MmCopyMemory)(buffer, target_address, size, MM_COPY_MEMORY_PHYSICAL, bytes);
}

PVOID split_memory(PVOID SearchBase, SIZE_T SearchSize, const void* Pattern, SIZE_T PatternSize)
{
	const UCHAR* searchBase = static_cast<const UCHAR*>(SearchBase);
	const UCHAR* pattern = static_cast<const UCHAR*>(Pattern);

	for (SIZE_T i = 0; i <= SearchSize - PatternSize; ++i) {
		SIZE_T j = 0;
		for (; j < PatternSize; ++j) {
			if (searchBase[i + j] != pattern[j])
				break;
		}
		if (j == PatternSize)
			return const_cast<UCHAR*>(&searchBase[i]);
	}

	return nullptr;
}

void* g_mmonp_MmPfnDatabase;

static NTSTATUS InitializeMmPfnDatabase()
{
	struct MmPfnDatabaseSearchPattern
	{
		const UCHAR* bytes;
		SIZE_T bytes_size;
		bool hard_coded;
	};

	MmPfnDatabaseSearchPattern patterns;

	// Windows 10 x64 Build 14332+
	static const UCHAR kPatternWin10x64[] = {
		0x48, 0x8B, 0xC1,
		0x48, 0xC1, 0xE8, 0x0C,
		0x48, 0x8D, 0x14, 0x40,
		0x48, 0x03, 0xD2,
		0x48, 0xB8,
	};

	patterns.bytes = kPatternWin10x64;
	patterns.bytes_size = sizeof(kPatternWin10x64);
	patterns.hard_coded = true;

	const auto p_MmGetVirtualForPhysical = reinterpret_cast<UCHAR*>(MmGetVirtualForPhysical);
	if (!p_MmGetVirtualForPhysical)
		return STATUS_PROCEDURE_NOT_FOUND;

	auto found = reinterpret_cast<UCHAR*>(split_memory(p_MmGetVirtualForPhysical, 0x20, patterns.bytes, patterns.bytes_size));
	if (!found)
		return STATUS_UNSUCCESSFUL;

	found += patterns.bytes_size;
	if (patterns.hard_coded) {
		g_mmonp_MmPfnDatabase = *reinterpret_cast<void**>(found);
	}
	else {
		const auto mmpfn_address = *reinterpret_cast<ULONG_PTR*>(found);
		g_mmonp_MmPfnDatabase = *reinterpret_cast<void**>(mmpfn_address);
	}

	g_mmonp_MmPfnDatabase = PAGE_ALIGN(g_mmonp_MmPfnDatabase);

	return STATUS_SUCCESS;
}

// KVA Shadow (Meltdown mitigation) awareness:
// On systems with KVA Shadow enabled, each process has TWO CR3 values:
//   - Kernel CR3: full page tables (kernel entries present)
//   - User CR3:   kernel entries masked out (PCID bits differ)
// The EPROCESS DirectoryTableBase field contains the kernel CR3.
// When reading usermode memory, we need the USER CR3 (or mask PCID bits).
// PCID bits are bits 0-11 of CR3. The NX bit (bit 63) may also be set.
#define CR3_PCID_MASK (0xFFFULL)
#define CR3_NOEXEC_BIT (0x1ULL << 63)

static bool g_KvaShadowEnabled = false;

static void DetectKvaShadow() {
	// Check if KVA Shadow is active by reading the shared page
	// KVA Shadow is enabled when the KVA_SHADOW bit is set in
	// SharedUserData->SystemCall (offset 0x308 in KUSER_SHARED_DATA)
	// Alternative: check nt!KiKvaShadow global variable
	PULONG kvaShadowFlag = (PULONG)((uintptr_t)0xFFFFF78000000000ULL + 0x308);
	if (kvaShadowFlag) {
		ULONG value = *kvaShadowFlag;
		g_KvaShadowEnabled = (value & 0x4) != 0; // KVA_SHADOW bit
	}
}

// Validate a candidate CR3 by walking the page tables for a known-good
// usermode address (the image base). Returns the validated CR3.
static uintptr_t validate_cr3(uintptr_t candidate_cr3, void* image_base) {
	if (!candidate_cr3 || !image_base)
		return 0;

	// Mask off PCID bits and NX bit to get the raw page table base
	uintptr_t raw_cr3 = candidate_cr3 & ~CR3_PCID_MASK & ~CR3_NOEXEC_BIT;
	if (!raw_cr3)
		return 0;

	// Try to translate the image base virtual address using this CR3
	uint64_t phys = Helper::translate_linear(raw_cr3, (uint64_t)image_base);
	if (!phys)
		return 0;

	// Verify the translated physical address actually contains an MZ header
	MMPTE check{};
	SIZE_T read_size = 0;
	if (!NT_SUCCESS(ReadPhysicalAddress(phys, &check, 2, &read_size)))
		return 0;

	uint16_t mz = *(uint16_t*)&check;
	if (mz != 0x5A4D) // 'MZ'
		return 0;

	return raw_cr3;
}

uintptr_t dirbase_from_base_address(void* base)
{
	if (!base)
		return 0;

	if (!g_KvaShadowEnabled)
		DetectKvaShadow();

	if (!NT_SUCCESS(InitializeMmPfnDatabase()))
		return 0;

	virt_addr_t virt_base{}; virt_base.value = base;

	size_t read{};

	auto ranges = MmGetPhysicalMemoryRanges();
	if (!ranges)
		return 0;

	for (int i = 0;; i++) {
		auto elem = &ranges[i];

		if (!elem->BaseAddress.QuadPart || !elem->NumberOfBytes.QuadPart)
			break;

		ULONG64 current_phys_address = elem->BaseAddress.QuadPart;

		for (int j = 0; j < (elem->NumberOfBytes.QuadPart / 0x1000); j++, current_phys_address += 0x1000) {

			_MMPFN* pnfinfo = (_MMPFN*)((uintptr_t)g_mmonp_MmPfnDatabase + (current_phys_address >> 12) * sizeof(_MMPFN));

			// 26100+: the kernel sets bit 54 (PrototypePte) in u4 for page-table pages,
			// so a raw 64-bit equality against (phys >> 12) never matches. The kernel
			// itself masks PteFrame to 40 bits before use (see MmGetVirtualForPhysical:
			// shr rax, 0xC / and rax, 0xFFFFFFFFFF). Compare the low 40 bits only.
			if ((pnfinfo->u4.PteFrame & 0xFFFFFFFFFF) == (current_phys_address >> 12)) {
				MMPTE pml4e{};
				if (!NT_SUCCESS(ReadPhysicalAddress(current_phys_address + 8 * virt_base.pml4_index, &pml4e, 8, &read)))
					continue;

				if (!pml4e.u.Hard.Valid)
					continue;

				MMPTE pdpte{};
				if (!NT_SUCCESS(ReadPhysicalAddress((pml4e.u.Hard.PageFrameNumber << 12) + 8 * virt_base.pdpt_index, &pdpte, 8, &read)))
					continue;

				if (!pdpte.u.Hard.Valid)
					continue;

				MMPTE pde{};
				if (!NT_SUCCESS(ReadPhysicalAddress((pdpte.u.Hard.PageFrameNumber << 12) + 8 * virt_base.pd_index, &pde, 8, &read)))
					continue;

				if (!pde.u.Hard.Valid)
					continue;

				MMPTE pte{};
				if (!NT_SUCCESS(ReadPhysicalAddress((pde.u.Hard.PageFrameNumber << 12) + 8 * virt_base.pt_index, &pte, 8, &read)))
					continue;

				if (!pte.u.Hard.Valid)
					continue;

				// Validate this CR3 by checking the image base translates to MZ
				uintptr_t validated = validate_cr3(current_phys_address, base);
				if (validated) {
					ExFreePool(ranges);
					return validated;
				}

				// If KVA Shadow is active, the user CR3 shares the same page-table
				// base but differs only in the PCID bits (bits 0-11). validate_cr3
				// masks those off anyway, so this variant is redundant — skip it.
			}
		}
	}

	ExFreePool(ranges);
	return 0;
}

NTSTATUS EacBypass::ReadProcessMemory(uint64_t dirbase, PVOID Address, PVOID AllocatedBuffer, SIZE_T size, SIZE_T* read)
{
	if (dirbase == 0 || !Address || !AllocatedBuffer || size == 0 || !read)
		return STATUS_INVALID_PARAMETER;

	SIZE_T CurOffset = 0;
	SIZE_T TotalSize = size;
	NTSTATUS NtRet = STATUS_SUCCESS;

	while (TotalSize)
	{
		uint64_t CurPhysAddr = Helper::translate_linear(dirbase, (ULONG64)Address + CurOffset);
		if (!CurPhysAddr)
			return STATUS_UNSUCCESSFUL;

		ULONG64 ReadSize = min(PAGE_SIZE - (CurPhysAddr & 0xFFF), TotalSize);
		SIZE_T BytesRead = 0;

		NtRet = ReadPhysicalAddress(CurPhysAddr, (PVOID)((ULONG64)AllocatedBuffer + CurOffset), ReadSize, &BytesRead);

		TotalSize -= BytesRead;
		CurOffset += BytesRead;

		if (NtRet != STATUS_SUCCESS || BytesRead == 0)
			break;
	}

	*read = CurOffset;
	return NtRet;
}

NTSTATUS EacBypass::DecryptCr3Packet(DTBStruct ControlRegister)
{
	if (!ControlRegister->process_id)
		return STATUS_UNSUCCESSFUL;

	PEPROCESS process = 0;
	if (spuf(PsLookupProcessByProcessId)((HANDLE)ControlRegister->process_id, &process) != STATUS_SUCCESS)
		return STATUS_UNSUCCESSFUL;

	struc::SavedCr3 = dirbase_from_base_address((void*)PsGetProcessSectionBaseAddress(process));

	ObfDereferenceObject(process);

	// Report failure honestly so usermode knows the handshake died
	// instead of silently storing 0 and returning STATUS_SUCCESS.
	if (!struc::SavedCr3)
		return STATUS_UNSUCCESSFUL;

	return STATUS_SUCCESS;
}
