#include "read.h"

NTSTATUS Read::read(PVOID target_address, PVOID buffer, SIZE_T size, SIZE_T* bytes_read) {
	MM_COPY_ADDRESS to_read = { 0 };
	to_read.PhysicalAddress.QuadPart = (LONGLONG)target_address;
	return spuf(MmCopyMemory)(buffer, to_read, size, MM_COPY_MEMORY_PHYSICAL, bytes_read);
}

NTSTATUS Read::readVirtual(PVOID target_address, PVOID buffer, SIZE_T size, SIZE_T* bytes_read) {
	MM_COPY_ADDRESS to_read = { 0 };
	to_read.PhysicalAddress.QuadPart = (LONGLONG)target_address;
	return spuf(MmCopyMemory)(buffer, to_read, size, MM_COPY_MEMORY_VIRTUAL, bytes_read);
}

NTSTATUS Read::ReadMemory(ReadStruct x) {
	if (!x->process_id)
		return STATUS_UNSUCCESSFUL;

	if (!struc::SavedCr3)
		return STATUS_UNSUCCESSFUL;

	if (!x->address || !x->buffer || !x->size)
		return STATUS_UNSUCCESSFUL;

	PEPROCESS process = NULL;
	spuf(PsLookupProcessByProcessId)((HANDLE)x->process_id, &process);
	if (!process)
		return STATUS_UNSUCCESSFUL;

	ObDereferenceObject(process);

	SIZE_T this_offset = 0;
	SIZE_T total_size = x->size;
	SIZE_T bytes_read_total = 0;

	while (this_offset < total_size) {
		INT64 physical_address = Helper::translate_linear(struc::SavedCr3, (ULONG64)x->address + this_offset);
		if (!physical_address)
			return STATUS_UNSUCCESSFUL;

		ULONG64 final_size = Helper::find_min(PAGE_SIZE - (physical_address & 0xFFF), total_size - this_offset);
		if (!final_size)
			break;

		SIZE_T bytes_trough = 0;
		NTSTATUS status = read(PVOID(physical_address), (PVOID)((ULONG64)x->buffer + this_offset), final_size, &bytes_trough);
		if (!NT_SUCCESS(status) || !bytes_trough)
			break;

		this_offset += bytes_trough;
		bytes_read_total += bytes_trough;
	}

	return (bytes_read_total > 0) ? STATUS_SUCCESS : STATUS_UNSUCCESSFUL;
}

NTSTATUS Read::ReadMemoryVirtual(ReadStruct x) {
	if (!x->process_id)
		return STATUS_UNSUCCESSFUL;

	if (!struc::SavedCr3)
		return STATUS_UNSUCCESSFUL;

	if (!x->address || !x->buffer || !x->size)
		return STATUS_UNSUCCESSFUL;

	PEPROCESS process = NULL;
	spuf(PsLookupProcessByProcessId)((HANDLE)x->process_id, &process);
	if (!process)
		return STATUS_UNSUCCESSFUL;

	ObDereferenceObject(process);

	SIZE_T this_offset = 0;
	SIZE_T total_size = x->size;
	SIZE_T bytes_read_total = 0;

	while (this_offset < total_size) {
		INT64 physical_address = Helper::translate_linear(struc::SavedCr3, (ULONG64)x->address + this_offset);
		if (!physical_address)
			return STATUS_UNSUCCESSFUL;

		ULONG64 final_size = Helper::find_min(PAGE_SIZE - (physical_address & 0xFFF), total_size - this_offset);
		if (!final_size)
			break;

		SIZE_T bytes_trough = 0;
		NTSTATUS status = readVirtual(PVOID(physical_address), (PVOID)((ULONG64)x->buffer + this_offset), final_size, &bytes_trough);
		if (!NT_SUCCESS(status) || !bytes_trough)
			break;

		this_offset += bytes_trough;
		bytes_read_total += bytes_trough;
	}

	return (bytes_read_total > 0) ? STATUS_SUCCESS : STATUS_UNSUCCESSFUL;
}
