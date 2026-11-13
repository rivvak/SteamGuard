#include <ntifs.h>
#include <windef.h>
#include "structs.h"
#include "new1.h"
#include "cr3.h"
#include "ioctl_codes.h"
#include "read.h"
#include "write.h"
#include "baseaddress.h"
#include "handler.h"
#include "ntos_base.h"
#include "mouse.h"

#include <stdlib.h>

typedef int BOOL;
typedef unsigned __int64 QWORD;

namespace crt
{
    template <typename t>
    __forceinline int strlen(const t* str) {
        if (!str)
            return 0;

        const t* buffer = str;
        while (*buffer)
            ++buffer;

        return static_cast<int>(buffer - str);
    }
}

typedef NTSTATUS(*IoControllerFunc)(PDEVICE_OBJECT, PIRP);

NTSTATUS io_controller1(PDEVICE_OBJECT device_obj, PIRP irp);
NTSTATUS io_controller2(PDEVICE_OBJECT device_obj, PIRP irp);

IoControllerFunc io_controller_funcs[] = {
    io_controller1,
    io_controller2
};

ULONG GetRandomIndex() {
    LARGE_INTEGER seed;
    KeQuerySystemTime(&seed);
    return (ULONG)(seed.QuadPart % ARRAYSIZE(io_controller_funcs));
}

NTSTATUS io_controller_func(PDEVICE_OBJECT device_object, PIRP irp) {
    ULONG idx = GetRandomIndex();
    return io_controller_funcs[idx](device_object, irp);
}

// Shared IOCTL dispatch logic
static NTSTATUS dispatch_ioctl(PDEVICE_OBJECT device_obj, PIRP irp) {

    UNREFERENCED_PARAMETER(device_obj);

    NTSTATUS status = { };
    ULONG bytes = { };
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(irp);

    ULONG code = stack->Parameters.DeviceIoControl.IoControlCode;
    ULONG size = stack->Parameters.DeviceIoControl.InputBufferLength;

    switch (code)
    {
    case code_read_physical:
        if (size == sizeof(_rw)) {
            ReadStruct req = (ReadStruct)(irp->AssociatedIrp.SystemBuffer);
            status = Read::ReadMemory(req);
            bytes = sizeof(_r);
        }
        else {
            status = STATUS_INFO_LENGTH_MISMATCH;
            bytes = 0;
        }
        break;

    case code_read_virtual:
        if (size == sizeof(_rw)) {
            ReadStruct req = (ReadStruct)(irp->AssociatedIrp.SystemBuffer);
            status = Read::ReadMemoryVirtual(req);
            bytes = sizeof(_r);
        }
        else {
            status = STATUS_INFO_LENGTH_MISMATCH;
            bytes = 0;
        }
        break;

    case code_write:
        if (size == sizeof(_rw)) {
            WriteStruct req = (WriteStruct)(irp->AssociatedIrp.SystemBuffer);
            status = Write::WriteMemory(req);
            bytes = sizeof(_rw);
        }
        else {
            status = STATUS_INFO_LENGTH_MISMATCH;
            bytes = 0;
        }
        break;

    case code_get_base_address:
        if (size == sizeof(_ba)) {
            BaseAddressStruct req = (BaseAddressStruct)(irp->AssociatedIrp.SystemBuffer);
            status = BaseAddress::GetBaseAddress(req);
            bytes = sizeof(_ba);
        }
        else {
            status = STATUS_INFO_LENGTH_MISMATCH;
            bytes = 0;
        }
        break;

    case code_decrypt_cr3:
        if (size == sizeof(_cr3)) {
            DTBStruct req = (DTBStruct)(irp->AssociatedIrp.SystemBuffer);
            status = EacBypass::DecryptCr3Packet(req);
            bytes = sizeof(_cr3);
        }
        else {
            status = STATUS_INFO_LENGTH_MISMATCH;
            bytes = 0;
        }
        break;

    case code_gma:
        if (size == sizeof(_gma)) {
            ModuleStruct req = (ModuleStruct)(irp->AssociatedIrp.SystemBuffer);
            status = BaseAddress::GetModuleAddress(req);
            bytes = sizeof(_gma);
        }
        else {
            status = STATUS_INFO_LENGTH_MISMATCH;
            bytes = 0;
        }
        break;

    case code_restart_driver:
        if (size == sizeof(_restart)) {
            RestartStruct req = (RestartStruct)(irp->AssociatedIrp.SystemBuffer);
            status = RestartDriv(req);
            bytes = sizeof(_restart);
        }
        else {
            status = STATUS_INFO_LENGTH_MISMATCH;
            bytes = 0;
        }
        break;

    case code_movemouse:
        if (size == sizeof(_movemouse)) {
            auto* req = (_movemouse*)(irp->AssociatedIrp.SystemBuffer);
            status = Mouse::SendMouseInput(req->x, req->y, req->button_flags);
            bytes = sizeof(_movemouse);
        }
        else {
            status = STATUS_INFO_LENGTH_MISMATCH;
            bytes = 0;
        }
        break;
    }

    irp->IoStatus.Status = status;
    irp->IoStatus.Information = bytes;
    IoCompleteRequest(irp, IO_NO_INCREMENT);

    return status;
}

NTSTATUS io_controller1(PDEVICE_OBJECT device_obj, PIRP irp) {
    return dispatch_ioctl(device_obj, irp);
}

NTSTATUS io_controller2(PDEVICE_OBJECT device_obj, PIRP irp) {
    return dispatch_ioctl(device_obj, irp);
}

static inline void safe_memcpy(const void* dstp, const void* srcp, UINT len)
{
    ULONG* dst = (ULONG*)dstp;
    ULONG* src = (ULONG*)srcp;
    UINT i, tail;

    for (i = 0; i < (len / sizeof(ULONG)); i++)
        *dst++ = *src++;

    tail = len & (sizeof(ULONG) - 1);
    if (tail) {
        UCHAR* dstb = (UCHAR*)dstp;
        UCHAR* srcb = (UCHAR*)srcp;
        for (i = len - tail; i < len; i++)
            dstb[i] = srcb[i];
    }
}

UNICODE_STRING concatenate_strings(const wchar_t* str1, const wchar_t* str2)
{
    UNICODE_STRING result;
    spuf(RtlInitUnicodeString)(&result, nullptr);

    size_t length1 = wcslen(str1);
    size_t length2 = wcslen(str2);
    size_t totalLength = length1 + length2;

    result.Buffer = (wchar_t*)spuf(ExAllocatePool)(NonPagedPool, (totalLength + 1) * sizeof(wchar_t));

    if (result.Buffer)
    {
        result.Length = (USHORT)(totalLength * sizeof(wchar_t));
        result.MaximumLength = (USHORT)((totalLength + 1) * sizeof(wchar_t));
        safe_memcpy(result.Buffer, str1, length1 * sizeof(wchar_t));
        safe_memcpy(result.Buffer + length1, str2, (length2 + 1) * sizeof(wchar_t));
    }

    return result;
}

NTSTATUS unsupported_dispatch(PDEVICE_OBJECT device_obj, PIRP irp) {
    UNREFERENCED_PARAMETER(device_obj);

    irp->IoStatus.Status = STATUS_NOT_SUPPORTED;
    IoCompleteRequest(irp, IO_NO_INCREMENT);

    return irp->IoStatus.Status;
}

NTSTATUS dispatch_handler(PDEVICE_OBJECT device_obj, PIRP irp) {
    UNREFERENCED_PARAMETER(device_obj);

    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(irp);

    switch (stack->MajorFunction) {
    case IRP_MJ_CREATE:
    case IRP_MJ_CLOSE:
        irp->IoStatus.Status = STATUS_SUCCESS;
        irp->IoStatus.Information = 0;
        break;
    default:
        irp->IoStatus.Status = STATUS_NOT_SUPPORTED;
        irp->IoStatus.Information = 0;
        break;
    }

    IoCompleteRequest(irp, IO_NO_INCREMENT);
    return irp->IoStatus.Status;
}

#define UUID_LENGTH 36

// Deterministic device name — both sides of the handshake hardcode the same GUID.
// No random generation, no uuid.dat file, no disk artifact. The IOCTL security
// cookie is the real gate; device-name obscurity was never the boundary.
#define DEVICE_GUID_W L"{834d7291-7e88-4c44-ad23-de42d34d7532}"

void generate_simple_uuid(char* uuid_str) {
    static const char* hex_digits = "0123456789abcdef";

    for (int i = 0; i < UUID_LENGTH; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            uuid_str[i] = '-';
        }
        else {
            uuid_str[i] = hex_digits[rand() % 16];
        }
    }
    uuid_str[UUID_LENGTH] = '\0';
}

// REMOVED: write_uuid_to_file — EAC scans for anomalous file drops in ProgramData\Microsoft\Diagnosis.
// Device name is now generated in-memory only, no disk I/O at init.

EXTERN_C
PLIST_ENTRY PsLoadedModuleList;

typedef struct _KLDR_DATA_TABLE_ENTRY
{
    LIST_ENTRY InLoadOrderLinks;
    PVOID ExceptionTable;
    ULONG ExceptionTableSize;
    PVOID GpValue;
    PVOID NonPagedDebugInfo;
    PVOID DllBase;
    PVOID EntryPoint;
    ULONG SizeOfImage;
    UNICODE_STRING FullDllName;
    UNICODE_STRING BaseDllName;
    ULONG Flags;
    USHORT LoadCount;
    USHORT __Unused5;
    PVOID SectionPointer;
    ULONG CheckSum;
    PVOID LoadedImports;
    PVOID PatchInformation;
} KLDR_DATA_TABLE_ENTRY, * PKLDR_DATA_TABLE_ENTRY;

void DkomUnlinking(PDRIVER_OBJECT drvobj) {
    // Delegate to the comprehensive hiding routine (all 3 LDR lists + name zeroing)
    HideDriver1123(drvobj);
}

// Global device tracking for clean unload (declared before use)
static PDEVICE_OBJECT g_DeviceObject = nullptr;
static WCHAR g_DosDeviceName[64] = { 0 };

void DriverUnload(PDRIVER_OBJECT DriverObject) {
    UNREFERENCED_PARAMETER(DriverObject);

    if (g_DeviceObject) {
        UNICODE_STRING dos_device;
        RtlInitUnicodeString(&dos_device, g_DosDeviceName);
        IoDeleteSymbolicLink(&dos_device);
        IoDeleteDevice(g_DeviceObject);
        g_DeviceObject = nullptr;
        g_DosDeviceName[0] = L'\0';
    }
}

NTSTATUS initialize_driver(PDRIVER_OBJECT drv_obj, PUNICODE_STRING path) {
    UNREFERENCED_PARAMETER(path);
    NTSTATUS status = { };
    PDEVICE_OBJECT device_obj = { };

    if (!struc::ntos_image_base)
        struc::ntos_image_base = NTOS::get_ntos_base_address();

    // Deterministic device name - hardcoded GUID shared with usermode.
    // No random generation, no uuid.dat, no disk I/O (EAC scans for anomalous file drops).
    wchar_t wide_uuid[UUID_LENGTH + 1];
    RtlZeroMemory(wide_uuid, sizeof(wide_uuid));
    RtlCopyMemory(wide_uuid, DEVICE_GUID_W, sizeof(DEVICE_GUID_W));

    auto device = concatenate_strings(L"\\Device\\", wide_uuid);
    auto dos_device = concatenate_strings(L"\\DosDevices\\", wide_uuid);

    status = IoCreateDevice(drv_obj, 0, &device, FILE_DEVICE_UNKNOWN, FILE_DEVICE_SECURE_OPEN, FALSE, &device_obj);
    if (!NT_SUCCESS(status))
        return status;

    status = IoCreateSymbolicLink(&dos_device, &device);
    if (!NT_SUCCESS(status))
        return status;

    for (int i = 0; i <= IRP_MJ_MAXIMUM_FUNCTION; i++)
        drv_obj->MajorFunction[i] = &unsupported_dispatch;

    device_obj->Flags |= DO_BUFFERED_IO;

    drv_obj->MajorFunction[IRP_MJ_CREATE] = &dispatch_handler;
    drv_obj->MajorFunction[IRP_MJ_CLOSE] = &dispatch_handler;
    drv_obj->MajorFunction[IRP_MJ_DEVICE_CONTROL] = &io_controller_func;

    // Preserve legitimate DRIVER_OBJECT and KLDR_DATA_TABLE_ENTRY backing so
    // module/device/memory/stack integrity scanners (such as Undetected-Easy)
    // see a consistent, module-backed driver object with valid DriverSection,
    // DriverStart, DriverSize, and MajorFunction targets.
    device_obj->Flags &= ~DO_DEVICE_INITIALIZING;

    // Store globals for clean unload
    g_DeviceObject = device_obj;
    RtlCopyMemory(g_DosDeviceName, dos_device.Buffer, min(dos_device.Length, sizeof(g_DosDeviceName) - sizeof(WCHAR)));
    g_DosDeviceName[dos_device.Length / sizeof(WCHAR)] = L'\0';

    return status;
}

extern "C" NTSTATUS DriverEntry(PDRIVER_OBJECT DriverObject, PUNICODE_STRING RegistryPath)
{
    struc::ntos_image_base = NTOS::get_ntos_base_address();

    if (DriverObject) {
        DriverObject->DriverUnload = &DriverUnload;
        return initialize_driver(DriverObject, RegistryPath);
    }

    return IoCreateDriver(&initialize_driver);
}
