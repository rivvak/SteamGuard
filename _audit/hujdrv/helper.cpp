#include "Helper.h"
#include "paging.h"

#define PAGE_OFFSET_SIZE 12
static const UINT64 PMASK = (~0xfull << 8) & 0xfffffffffull;

struct cache {
	uintptr_t Address;
	MMPTE Value;
};

static cache cached_pml4e[512];
static cache cached_pdpte[512];
static cache cached_pde[512];
static cache cached_pte[512];

UINT64 Helper::translate_linear(UINT64 directoryTableBase, UINT64 virtualAddress) {
	_virt_addr_t virtual_address{};
	virtual_address.value = PVOID(virtualAddress);

	SIZE_T Size = 0;

	// PML4
	MMPTE pml4e{};
	UINT64 pml4e_addr = directoryTableBase + 8 * virtual_address.pml4_index;
	if (!NT_SUCCESS(Read::read(PVOID(pml4e_addr), &pml4e, 8, &Size)) || !pml4e.u.Hard.Valid)
		return 0;

	// PDPT
	MMPTE pdpte{};
	UINT64 pdpte_addr = (pml4e.u.Hard.PageFrameNumber << 12) + 8 * virtual_address.pdpt_index;
	if (!NT_SUCCESS(Read::read(PVOID(pdpte_addr), &pdpte, 8, &Size)) || !pdpte.u.Hard.Valid)
		return 0;

	// 1GB Large Page check
	if (pdpte.u.Hard.LargePage) {
		return (pdpte.u.Hard.PageFrameNumber << 12) + (virtualAddress & 0x3FFFFFFFULL);
	}

	// PDE
	MMPTE pde{};
	UINT64 pde_addr = (pdpte.u.Hard.PageFrameNumber << 12) + 8 * virtual_address.pd_index;
	if (!NT_SUCCESS(Read::read(PVOID(pde_addr), &pde, 8, &Size)) || !pde.u.Hard.Valid)
		return 0;

	// 2MB Large Page check (common in UE heap / TArray buffers)
	if (pde.u.Hard.LargePage) {
		return (pde.u.Hard.PageFrameNumber << 12) + (virtualAddress & 0x1FFFFFULL);
	}

	// 4KB PTE
	MMPTE pte{};
	UINT64 pte_addr = (pde.u.Hard.PageFrameNumber << 12) + 8 * virtual_address.pt_index;
	if (!NT_SUCCESS(Read::read(PVOID(pte_addr), &pte, 8, &Size)) || !pte.u.Hard.Valid)
		return 0;

	return (pte.u.Hard.PageFrameNumber << 12) + virtual_address.offset;
}

ULONG64 Helper::find_min(INT32 g, SIZE_T f) {
	INT32 h = (INT32)f;
	ULONG64 result = 0;
	result = (((g) < (h)) ? (g) : (h));
	return result;
}
