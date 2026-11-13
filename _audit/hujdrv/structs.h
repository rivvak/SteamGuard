#pragma once
#include <ntifs.h>
#include <windef.h>
#include "imports.h"
#include "callstack.h"


typedef struct _rw {
	INT32 process_id;
	ULONGLONG address;
	ULONGLONG buffer;
	ULONGLONG size;
} rw, * WriteStruct;

typedef struct _gma {
	INT32 process_id;
	ULONGLONG* address;
	char module_name[256];
} _gma, * ModuleStruct;

typedef struct _r {
	INT32 process_id;
	ULONGLONG address;
	ULONGLONG buffer;
	ULONGLONG size;
} rwr, * ReadStruct;

typedef struct _hf {
	INT32 process_id;
} hidefile, * HideFileStruct;


typedef struct _ba {
	INT32 process_id;
	ULONGLONG* address;
} ba, * BaseAddressStruct;

typedef struct _cr3 {
	INT32 process_id;
} cr3, * DTBStruct;

typedef struct _restart {
	bool bResetCr3;
} restart, * RestartStruct;

typedef struct _movemouse {
	long x;
	long y;
	unsigned short button_flags;
} movemouse, * MouseMovementStruct;





typedef struct _SYSTEM_BIGPOOL_ENTRY
{
	union {
		PVOID VirtualAddress;
		ULONG_PTR NonPaged : 1;
	};
	ULONG_PTR SizeInBytes;
	union {
		UCHAR Tag[4];
		ULONG TagUlong;
	};
} SYSTEM_BIGPOOL_ENTRY, * PSYSTEM_BIGPOOL_ENTRY;

typedef struct _SYSTEM_BIGPOOL_INFORMATION {
	ULONG Count;
	SYSTEM_BIGPOOL_ENTRY AllocatedInfo[ANYSIZE_ARRAY];
} SYSTEM_BIGPOOL_INFORMATION, * PSYSTEM_BIGPOOL_INFORMATION;

typedef enum _SYSTEM_INFORMATION_CLASS {
	SystemBigPoolInformation = 0x42
} SYSTEM_INFORMATION_CLASS;

typedef struct _LDR_DATA_TABLE_ENTRY
{
	LIST_ENTRY InLoadOrderModuleList;
	LIST_ENTRY InMemoryOrderModuleList;
	LIST_ENTRY InInitializationOrderModuleList;
	PVOID DllBase;
	PVOID EntryPoint;
	ULONG SizeOfImage;
	UNICODE_STRING FullDllName;
	UNICODE_STRING BaseDllName;
	ULONG Flags;
	USHORT LoadCount;
	USHORT TlsIndex;
	LIST_ENTRY HashLinks;
	PVOID SectionPointer;
	ULONG CheckSum;
	ULONG TimeDateStamp;
} LDR_DATA_TABLE_ENTRY, * PLDR_DATA_TABLE_ENTRY;

extern "C"
{
	PLIST_ENTRY NTKERNELAPI PsLoadedModuleList;
	NTKERNELAPI PVOID NTAPI RtlFindExportedRoutineByName(_In_ PVOID ImageBase, _In_ PCCH RoutineName);
	//NTSTATUS NTAPI IoCreateDriver(PUNICODE_STRING DriverName, PDRIVER_INITIALIZE InitializationFunction);
	PVOID NTAPI PsGetProcessSectionBaseAddress(PEPROCESS Process);
	NTSTATUS NTAPI ZwQuerySystemInformation(SYSTEM_INFORMATION_CLASS systemInformationClass, PVOID systemInformation, ULONG systemInformationLength, PULONG returnLength);
	NTSYSAPI NTSTATUS NTAPI ZwQueryInformationProcess(__in HANDLE ProcessHandle, __in PROCESSINFOCLASS ProcessInformationClass, __out_bcount(ProcessInformationLength) PVOID ProcessInformation, __in ULONG ProcessInformationLength, __out_opt PULONG ReturnLength);
	NTSYSAPI NTSTATUS NTAPI ObReferenceObjectByName(_In_ PUNICODE_STRING ObjectName, _In_ ULONG Attributes, _In_opt_ PACCESS_STATE AccessState, _In_opt_ ACCESS_MASK DesiredAccess, _In_ POBJECT_TYPE ObjectType, _In_ KPROCESSOR_MODE AccessMode, _Inout_opt_ PVOID ParseContext, _Out_ PVOID* Object);
};

