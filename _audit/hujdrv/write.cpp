#include "write.h"
#include "cr3.h"
#include "ioctl_codes.h"
#include "Helper.h"

NTSTATUS Write::write(PVOID target_address, PVOID buffer, SIZE_T size, SIZE_T* bytes_read)
{
	if (!target_address)
		return STATUS_UNSUCCESSFUL;

	PHYSICAL_ADDRESS AddrToWrite = { 0 };
	AddrToWrite.QuadPart = LONGLONG(target_address);

	PVOID pmapped_mem = MmMapIoSpaceEx(AddrToWrite, size, PAGE_READWRITE);

	if (!pmapped_mem)
		return STATUS_UNSUCCESSFUL;

	memcpy(pmapped_mem, buffer, size);

	*bytes_read = size;
	MmUnmapIoSpace(pmapped_mem, size);
	return STATUS_SUCCESS;
}

NTSTATUS Write::WriteMemory(WriteStruct x) {
	if (!x->process_id)
		return STATUS_UNSUCCESSFUL;

	if (!struc::SavedCr3)
		return STATUS_UNSUCCESSFUL;

	if (!x->address || !x->buffer || !x->size)
		return STATUS_UNSUCCESSFUL;

	PEPROCESS process = NULL;
	PsLookupProcessByProcessId((HANDLE)x->process_id, &process);
	if (!process)
		return STATUS_UNSUCCESSFUL;

	ObDereferenceObject(process);

	SIZE_T this_offset = 0;
	SIZE_T total_size = x->size;
	SIZE_T bytes_written_total = 0;

	while (this_offset < total_size) {
		INT64 physical_address = Helper::translate_linear(struc::SavedCr3, (ULONG64)x->address + this_offset);
		if (!physical_address)
			return STATUS_UNSUCCESSFUL;

		ULONG64 final_size = Helper::find_min(PAGE_SIZE - (physical_address & 0xFFF), total_size - this_offset);
		if (!final_size)
			break;

		SIZE_T bytes_trough = 0;
		NTSTATUS status = write(PVOID(physical_address), (PVOID)((ULONG64)x->buffer + this_offset), final_size, &bytes_trough);
		if (!NT_SUCCESS(status) || !bytes_trough)
			break;

		this_offset += bytes_trough;
		bytes_written_total += bytes_trough;
	}

	return (bytes_written_total > 0) ? STATUS_SUCCESS : STATUS_UNSUCCESSFUL;
}
