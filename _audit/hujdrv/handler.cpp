#include "handler.h"

NTSTATUS RestartDriv(RestartStruct x)
{
	if (x->bResetCr3)
	{
		struc::SavedCr3 = 0;
	}

	return STATUS_SUCCESS;
}
