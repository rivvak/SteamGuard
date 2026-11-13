
#pragma once


#include <ntifs.h>
#include <ntstrsafe.h>
#include <windef.h>
#include "new.h"





NTSTATUS NTAPI IopInvalidDeviceRequest(_In_ PDEVICE_OBJECT DeviceObject, _In_ PIRP Irp)
{
	UNREFERENCED_PARAMETER(DeviceObject);
	Irp->IoStatus.Status = STATUS_INVALID_DEVICE_REQUEST;
	Irp->IoStatus.Information = 0;
	IoCompleteRequest(Irp, IO_NO_INCREMENT);
	return STATUS_INVALID_DEVICE_REQUEST;
}





void HideDriver1123(PDRIVER_OBJECT DriverObject) {
	PLDR_DATA_TABLE_ENTRY entry = (PLDR_DATA_TABLE_ENTRY)DriverObject->DriverSection;
	if (!entry) {
		return;
	}
	RemoveEntryList(&entry->InLoadOrderModuleList);
	RemoveEntryList(&entry->InMemoryOrderModuleList);
	RemoveEntryList(&entry->InInitializationOrderModuleList);
	InitializeListHead(&entry->InLoadOrderModuleList);
	InitializeListHead(&entry->InMemoryOrderModuleList);
	InitializeListHead(&entry->InInitializationOrderModuleList);

	// Zero name buffers so pool scanning can't find leftover strings
	if (entry->FullDllName.Buffer) {
		RtlSecureZeroMemory(entry->FullDllName.Buffer, entry->FullDllName.MaximumLength);
		entry->FullDllName.Buffer = nullptr;
		entry->FullDllName.Length = 0;
		entry->FullDllName.MaximumLength = 0;
	}
	if (entry->BaseDllName.Buffer) {
		RtlSecureZeroMemory(entry->BaseDllName.Buffer, entry->BaseDllName.MaximumLength);
		entry->BaseDllName.Buffer = nullptr;
		entry->BaseDllName.Length = 0;
		entry->BaseDllName.MaximumLength = 0;
	}

	// Zero the driver object name so \Driver\ enumeration finds nothing
	if (DriverObject->DriverName.Buffer) {
		RtlSecureZeroMemory(DriverObject->DriverName.Buffer, DriverObject->DriverName.MaximumLength);
		DriverObject->DriverName.Length = 0;
		DriverObject->DriverName.MaximumLength = 0;
		DriverObject->DriverName.Buffer = nullptr;
	}
	DriverObject->DriverSection = nullptr;
}



NTSTATUS __fastcall IoCreateDriver(_In_ NTSTATUS(__fastcall* EntryPoint)(_In_ PDRIVER_OBJECT, _In_ PUNICODE_STRING))
{
	
	HANDLE drv_handle;
	USHORT name_length;
	WCHAR name_buffer[100];
	PDRIVER_OBJECT drv_obj;
	OBJECT_ATTRIBUTES obj_attribs;
	UNICODE_STRING local_drv_name;
	UNICODE_STRING service_key_name;
	NTSTATUS status = STATUS_SUCCESS;
	ULONG obj_size = sizeof(DRIVER_OBJECT) + sizeof(EXTENDED_DRIVER_EXTENSION);


	
	name_length = (USHORT)swprintf(name_buffer, L"\\Driver\\%08u", (ULONG)KeQueryUnbiasedInterruptTime());
	local_drv_name.Length = name_length * sizeof(WCHAR);
	local_drv_name.MaximumLength = local_drv_name.Length + sizeof(UNICODE_NULL);
	local_drv_name.Buffer = name_buffer;


	
	InitializeObjectAttributes(&obj_attribs, &local_drv_name, OBJ_PERMANENT | OBJ_CASE_INSENSITIVE | OBJ_KERNEL_HANDLE, NULL, NULL);


	
	status = ObCreateObject(KernelMode, *IoDriverObjectType, &obj_attribs, KernelMode, NULL, obj_size, 0, 0, (PVOID*)&drv_obj);
	if (!NT_SUCCESS(status))
		return status;


	
	RtlZeroMemory(drv_obj, obj_size);				
	drv_obj->Type = IO_TYPE_DRIVER;				
	drv_obj->Size = sizeof(DRIVER_OBJECT);			
	drv_obj->Flags = DRVO_BUILTIN_DRIVER;									
	drv_obj->DriverExtension = (PDRIVER_EXTENSION)(drv_obj + 1);	
	drv_obj->DriverExtension->DriverObject = drv_obj;		
	drv_obj->DriverInit = EntryPoint;			



	for (int i = 0; i <= IRP_MJ_MAXIMUM_FUNCTION; i++)
	{
		drv_obj->MajorFunction[i] = IopInvalidDeviceRequest;
	}


	
	service_key_name.MaximumLength = local_drv_name.Length + sizeof(UNICODE_NULL);
	service_key_name.Buffer = (PWCH)ExAllocatePool2(POOL_FLAG_PAGED, local_drv_name.MaximumLength, (ULONG)KeQueryUnbiasedInterruptTime());
	if (!service_key_name.Buffer)
	{
		ObMakeTemporaryObject(drv_obj);
		ObfDereferenceObject(drv_obj);
		return STATUS_INSUFFICIENT_RESOURCES;
	}
	RtlCopyUnicodeString(&service_key_name, &local_drv_name);
	service_key_name.Buffer[service_key_name.Length / sizeof(WCHAR)] = UNICODE_NULL;
	drv_obj->DriverExtension->ServiceKeyName = service_key_name;


	
	drv_obj->DriverName.MaximumLength = local_drv_name.Length;
	drv_obj->DriverName.Buffer = (PWCH)ExAllocatePool2(POOL_FLAG_PAGED, drv_obj->DriverName.MaximumLength, (ULONG)KeQueryUnbiasedInterruptTime());
	if (!drv_obj->DriverName.Buffer)
	{
		ObMakeTemporaryObject(drv_obj);
		ObfDereferenceObject(drv_obj);
		return STATUS_INSUFFICIENT_RESOURCES;
	}
	RtlCopyUnicodeString(&drv_obj->DriverName, &local_drv_name);


	
	status = ObInsertObject(drv_obj, NULL, FILE_READ_DATA, 0, NULL, &drv_handle);
	ZwClose(drv_handle);
	if (!NT_SUCCESS(status))
	{
		ObMakeTemporaryObject(drv_obj);
		ObfDereferenceObject(drv_obj);
		return status;
	}


	
	status = EntryPoint(drv_obj, NULL);
	if (!NT_SUCCESS(status))
	{
		ObMakeTemporaryObject(drv_obj);
		ObDereferenceObject(drv_obj);
		return status;
	}


	
	for (int i = 0; i <= IRP_MJ_MAXIMUM_FUNCTION; i++)
	{
		if (!drv_obj->MajorFunction[i])
		{
			drv_obj->MajorFunction[i] = IopInvalidDeviceRequest;
		}
	}




	HideDriver1123(drv_obj);

	return status; 
}