#pragma once
#include <ntifs.h>
#include <windef.h>
#include "structs.h"
#include "helper.h"



namespace EacBypass
{
	
	NTSTATUS DecryptCr3Packet(DTBStruct ControlRegister);
	NTSTATUS ReadProcessMemory(uint64_t dirbase, PVOID Address, PVOID AllocatedBuffer, SIZE_T size, SIZE_T* read);
}