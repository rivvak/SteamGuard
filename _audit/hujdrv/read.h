#pragma once
#include <ntifs.h>
#include <windef.h>
#include "structs.h"
#include "ioctl_codes.h"
#include "cr3.h"
#include "Helper.h"
//#include <driver essentials/import.h>


namespace Read
{
	NTSTATUS ReadMemory(ReadStruct x);
	NTSTATUS ReadMemoryVirtual(ReadStruct x);
	NTSTATUS read(PVOID target_address, PVOID buffer, SIZE_T size, SIZE_T* bytes_read);
	NTSTATUS readVirtual(PVOID target_address, PVOID buffer, SIZE_T size, SIZE_T* bytes_read);
}