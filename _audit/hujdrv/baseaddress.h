#pragma once
#include <ntifs.h>
#include <windef.h>
#include "structs.h"
#include "ioctl_codes.h"


namespace BaseAddress
{
	NTSTATUS GetBaseAddress(BaseAddressStruct x);
	NTSTATUS GetModuleAddress(ModuleStruct x);

}