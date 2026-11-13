#pragma once
#include <ntifs.h>
#include <windef.h>
#include "read.h"

namespace Helper
{
	UINT64 translate_linear(UINT64 directoryTableBase, UINT64 virtualAddress);
	ULONG64 find_min(INT32 g, SIZE_T f);
}