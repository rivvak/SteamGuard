#pragma once
#include <ntifs.h>
#include <windef.h>
#include <cstdint>

// Page table entry structures shared between cr3.cpp and helper.cpp

typedef union _virt_addr_t
{
	void* value;
	struct
	{
		uintptr_t offset : 12;
		uintptr_t pt_index : 9;
		uintptr_t pd_index : 9;
		uintptr_t pdpt_index : 9;
		uintptr_t pml4_index : 9;
		uintptr_t reserved : 16;
	};
} virt_addr_t, * pvirt_addr_t;

typedef struct _MI_ACTIVE_PFN
{
	union
	{
		struct
		{
			struct
			{
				unsigned __int64 Tradable : 1;
				unsigned __int64 NonPagedBuddy : 43;
			};
		} Leaf;
		struct
		{
			struct
			{
				unsigned __int64 Tradable : 1;
				unsigned __int64 WsleAge : 3;
				unsigned __int64 OldestWsleLeafEntries : 10;
				unsigned __int64 OldestWsleLeafAge : 3;
				unsigned __int64 NonPagedBuddy : 43;
			};
		} PageTable;
		unsigned __int64 EntireActiveField;
	};
} MI_ACTIVE_PFN, * PMI_ACTIVE_PFN;

typedef struct _MMPTE_HARDWARE
{
	struct
	{
		unsigned __int64 Valid : 1;
		unsigned __int64 Dirty1 : 1;
		unsigned __int64 Owner : 1;
		unsigned __int64 WriteThrough : 1;
		unsigned __int64 CacheDisable : 1;
		unsigned __int64 Accessed : 1;
		unsigned __int64 Dirty : 1;
		unsigned __int64 LargePage : 1;
		unsigned __int64 Global : 1;
		unsigned __int64 CopyOnWrite : 1;
		unsigned __int64 Unused : 1;
		unsigned __int64 Write : 1;
		unsigned __int64 PageFrameNumber : 40;
		unsigned __int64 ReservedForSoftware : 4;
		unsigned __int64 WsleAge : 4;
		unsigned __int64 WsleProtection : 3;
		unsigned __int64 NoExecute : 1;
	};
} MMPTE_HARDWARE, * PMMPTE_HARDWARE;

typedef struct _MMPTE_PROTOTYPE
{
	struct
	{
		unsigned __int64 Valid : 1;
		unsigned __int64 DemandFillProto : 1;
		unsigned __int64 HiberVerifyConverted : 1;
		unsigned __int64 ReadOnly : 1;
		unsigned __int64 SwizzleBit : 1;
		unsigned __int64 Protection : 5;
		unsigned __int64 Prototype : 1;
		unsigned __int64 Combined : 1;
		unsigned __int64 Unused1 : 4;
		__int64 ProtoAddress : 48;
	};
} MMPTE_PROTOTYPE, * PMMPTE_PROTOTYPE;

typedef struct _MMPTE_SOFTWARE
{
	struct
	{
		unsigned __int64 Valid : 1;
		unsigned __int64 PageFileReserved : 1;
		unsigned __int64 PageFileAllocated : 1;
		unsigned __int64 ColdPage : 1;
		unsigned __int64 SwizzleBit : 1;
		unsigned __int64 Protection : 5;
		unsigned __int64 Prototype : 1;
		unsigned __int64 Transition : 1;
		unsigned __int64 PageFileLow : 4;
		unsigned __int64 UsedPageTableEntries : 10;
		unsigned __int64 ShadowStack : 1;
		unsigned __int64 Unused : 5;
		unsigned __int64 PageFileHigh : 32;
	};
} MMPTE_SOFTWARE, * PMMPTE_SOFTWARE;

typedef struct _MMPTE_TIMESTAMP
{
	struct
	{
		unsigned __int64 MustBeZero : 1;
		unsigned __int64 Unused : 3;
		unsigned __int64 SwizzleBit : 1;
		unsigned __int64 Protection : 5;
		unsigned __int64 Prototype : 1;
		unsigned __int64 Transition : 1;
		unsigned __int64 PageFileLow : 4;
		unsigned __int64 Reserved : 16;
		unsigned __int64 GlobalTimeStamp : 32;
	};
} MMPTE_TIMESTAMP, * PMMPTE_TIMESTAMP;

typedef struct _MMPTE_TRANSITION
{
	struct
	{
		unsigned __int64 Valid : 1;
		unsigned __int64 Write : 1;
		unsigned __int64 Spare : 1;
		unsigned __int64 IoTracker : 1;
		unsigned __int64 SwizzleBit : 1;
		unsigned __int64 Protection : 5;
		unsigned __int64 Prototype : 1;
		unsigned __int64 Transition : 1;
		unsigned __int64 PageFrameNumber : 40;
		unsigned __int64 Unused : 12;
	};
} MMPTE_TRANSITION, * PMMPTE_TRANSITION;

typedef struct _MMPTE_SUBSECTION
{
	struct
	{
		unsigned __int64 Valid : 1;
		unsigned __int64 Unused0 : 3;
		unsigned __int64 SwizzleBit : 1;
		unsigned __int64 Protection : 5;
		unsigned __int64 Prototype : 1;
		unsigned __int64 ColdPage : 1;
		unsigned __int64 Unused1 : 3;
		unsigned __int64 ExecutePrivilege : 1;
		__int64 SubsectionAddress : 48;
	};
} MMPTE_SUBSECTION, * PMMPTE_SUBSECTION;

typedef struct _MMPTE_LIST
{
	struct
	{
		unsigned __int64 Valid : 1;
		unsigned __int64 OneEntry : 1;
		unsigned __int64 filler0 : 2;
		unsigned __int64 SwizzleBit : 1;
		unsigned __int64 Protection : 5;
		unsigned __int64 Prototype : 1;
		unsigned __int64 Transition : 1;
		unsigned __int64 filler1 : 16;
		unsigned __int64 NextEntry : 36;
	};
} MMPTE_LIST, * PMMPTE_LIST;

typedef struct _MMPTE
{
	union
	{
		union
		{
			unsigned __int64 Long;
			volatile unsigned __int64 VolatileLong;
			struct _MMPTE_HARDWARE Hard;
			struct _MMPTE_PROTOTYPE Proto;
			struct _MMPTE_SOFTWARE Soft;
			struct _MMPTE_TIMESTAMP TimeStamp;
			struct _MMPTE_TRANSITION Trans;
			struct _MMPTE_SUBSECTION Subsect;
			struct _MMPTE_LIST List;
		};
	} u;
} MMPTE, * PMMPTE;
