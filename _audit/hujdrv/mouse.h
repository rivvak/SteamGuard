#pragma once
#include <ntifs.h>
#include <windef.h>

extern "C" PLIST_ENTRY PsLoadedModuleList;

// =============================================================================
//  Mouse Input via Mouclass Circular Buffer Injection
//
//  Instead of calling MouseClassServiceCallback directly, we write
//  MOUSE_INPUT_DATA entries into mouclass's internal circular queue
//  or complete pending read IRPs. This avoids hooking the callback
//  and is harder to signature.
//
//  EAC ExtraInformation is still extracted and applied to each input.
// =============================================================================

namespace Mouse {

    // ---- Mouse Input Data (matches Windows MOUSE_INPUT_DATA) ----
    typedef struct _MOUSE_INPUT_DATA {
        USHORT UnitId;
        USHORT Flags;
        union {
            ULONG Buttons;
            struct {
                USHORT ButtonFlags;
                USHORT ButtonData;
            };
        };
        ULONG RawButtons;
        LONG LastX;
        LONG LastY;
        ULONG ExtraInformation;
    } MOUSE_INPUT_DATA, * PMOUSE_INPUT_DATA;

    // ---- Mouclass device extension offsets ----
    // These offsets target the mouclass.sys CLASS_DEVICE_EXTENSION layout
    // and are consistent across Windows 10/11 builds.
    #define MOUCLASS_DEVEXT_INPUT_COUNT      0x54
    #define MOUCLASS_DEVEXT_DATA_QUEUE_BASE  0x68
    #define MOUCLASS_DEVEXT_WRITE_POINTER    0x70
    #define MOUCLASS_DEVEXT_READ_POINTER     0x78
    #define MOUCLASS_DEVEXT_QUEUE_SIZE       0x88
    #define MOUCLASS_DEVEXT_SPINLOCK         0x90
    #define MOUCLASS_DEVEXT_PENDING_IRP      0x98

    // ---- Globals ----
    inline PDEVICE_OBJECT g_MouseClassDevice = nullptr;
    inline PFILE_OBJECT   g_MouseFileObject = nullptr;
    inline PVOID          g_MouseDeviceExtension = nullptr;
    inline BOOLEAN        g_Initialized = FALSE;
    inline UINT32         g_ExtraInformation = 0;

    // ---- Helper: Get kernel module base by name ----
    inline PVOID GetModuleBaseAddress(const wchar_t* moduleName) {
        UNICODE_STRING targetName;
        RtlInitUnicodeString(&targetName, moduleName);

        auto currentEntry = ::PsLoadedModuleList->Flink;
        while (currentEntry != ::PsLoadedModuleList) {
            auto entry = CONTAINING_RECORD(currentEntry,
                LDR_DATA_TABLE_ENTRY, InLoadOrderModuleList);

            if (RtlCompareUnicodeString(&entry->BaseDllName, &targetName, TRUE) == 0) {
                return entry->DllBase;
            }
            currentEntry = currentEntry->Flink;
        }
        return nullptr;
    }

    // ---- Helper: Get PE section by name ----
    typedef struct _SECTION_INFO {
        ULONG VirtualAddress;
        union {
            ULONG PhysicalAddress;
            ULONG VirtualSize;
        } Misc;
    } SECTION_INFO;

    inline SECTION_INFO GetSectionByName(PVOID moduleBase, const char* sectionName) {
        SECTION_INFO result = {};
        if (!moduleBase) return result;

        auto dosHeader = (PIMAGE_DOS_HEADER)moduleBase;
        if (dosHeader->e_magic != IMAGE_DOS_SIGNATURE) return result;

        auto ntHeaders = (PIMAGE_NT_HEADERS64)((UCHAR*)moduleBase + dosHeader->e_lfanew);
        if (ntHeaders->Signature != IMAGE_NT_SIGNATURE) return result;

        auto section = IMAGE_FIRST_SECTION(ntHeaders);
        for (USHORT i = 0; i < ntHeaders->FileHeader.NumberOfSections; i++, section++) {
            bool match = true;
            for (int j = 0; j < 8 && sectionName[j]; j++) {
                if (section->Name[j] != (UCHAR)sectionName[j]) {
                    match = false;
                    break;
                }
            }
            if (match) {
                result.VirtualAddress = section->VirtualAddress;
                result.Misc.VirtualSize = section->Misc.VirtualSize;
                return result;
            }
        }
        return result;
    }

    // ---- Helper: Pattern scan in memory region ----
    inline PVOID FindPattern(PVOID base, ULONG size,
        const char* pattern, const char* mask) {
        auto patLen = strlen(mask);
        auto data = (UCHAR*)base;

        for (ULONG i = 0; i + patLen <= size; i++) {
            bool found = true;
            for (ULONG j = 0; j < patLen; j++) {
                if (mask[j] == 'x' && data[i + j] != (UCHAR)pattern[j]) {
                    found = false;
                    break;
                }
            }
            if (found) return (PVOID)(data + i);
        }
        return nullptr;
    }

    // ---- Get EAC ExtraInformation value ----
    inline UINT32 GetExtraInformation() {
        if (g_ExtraInformation) return g_ExtraInformation;

        auto EACBase = GetModuleBaseAddress(L"EasyAntiCheat_EOS.sys");
        if (EACBase) {
            auto DataSection = GetSectionByName(EACBase, ".data");
            if (DataSection.VirtualAddress && DataSection.Misc.VirtualSize) {
                auto FoundAddress = FindPattern(
                    (PVOID)((UINT64)EACBase + DataSection.VirtualAddress),
                    DataSection.Misc.VirtualSize,
                    "\xFF\xFF\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xFF\x87",
                    "xxxxxxxxxx?????????xx");

                if (FoundAddress) {
                    UINT32 val = *(UINT32*)((UCHAR*)FoundAddress + 0xa);
                    val = ~val;
                    val = (val >> 23) | (val << (32 - 23));
                    g_ExtraInformation = ~val;
                }
            }
        }

        return g_ExtraInformation;
    }

    // ---- Set ExtraInformation from usermode (raw input capture fallback) ----
    inline void SetExtraInformation(UINT32 extraInfo) {
        if (extraInfo != 0) {
            g_ExtraInformation = extraInfo;
        }
    }

    // ---- Initialize Mouse: find mouclass device and validate circular buffer ----
    inline NTSTATUS InitializeMouse() {
        NTSTATUS status;
        UNICODE_STRING deviceName;

        RtlInitUnicodeString(&deviceName, L"\\Device\\PointerClass0");

        status = IoGetDeviceObjectPointer(
            &deviceName,
            FILE_READ_ATTRIBUTES,
            &g_MouseFileObject,
            &g_MouseClassDevice
        );

        if (!NT_SUCCESS(status)) {
            return status;
        }

        g_MouseDeviceExtension = g_MouseClassDevice->DeviceExtension;

        // Validate the queue exists and has a sane size
        ULONG queueSize = *(PULONG)((PUCHAR)g_MouseDeviceExtension + MOUCLASS_DEVEXT_QUEUE_SIZE);
        if (queueSize == 0 || queueSize > 0x100000) {
            ObDereferenceObject(g_MouseFileObject);
            g_MouseFileObject = NULL;
            g_MouseClassDevice = NULL;
            g_MouseDeviceExtension = NULL;
            return STATUS_INVALID_PARAMETER;
        }

        g_Initialized = TRUE;
        return STATUS_SUCCESS;
    }

    // ---- Send Mouse Input via circular buffer injection ----
    inline NTSTATUS SendMouseInput(LONG deltaX, LONG deltaY, USHORT buttonFlags) {
        if (!g_Initialized || !g_MouseDeviceExtension) {
            NTSTATUS init = InitializeMouse();
            if (!NT_SUCCESS(init)) return init;
        }

        PUCHAR devExt = (PUCHAR)g_MouseDeviceExtension;
        PKSPIN_LOCK spinLock = (PKSPIN_LOCK)(devExt + MOUCLASS_DEVEXT_SPINLOCK);
        KIRQL oldIrql;

        // Build input data with EAC ExtraInformation
        UINT32 extraInfo = GetExtraInformation();

        MOUSE_INPUT_DATA inputData = {};
        inputData.UnitId = 0;
        inputData.Flags = 0; // MOUSE_MOVE_RELATIVE
        inputData.ButtonFlags = buttonFlags;
        inputData.ButtonData = 0;
        inputData.RawButtons = 0;
        inputData.LastX = deltaX;
        inputData.LastY = deltaY;
        inputData.ExtraInformation = extraInfo;

        PIRP pendingIrp = NULL;
        ULONG bytesToCopy = 0;

        KeAcquireSpinLock(spinLock, &oldIrql);

        // Check if mouclass has a pending read IRP waiting for input
        PLIST_ENTRY pendingQueue = (PLIST_ENTRY)(devExt + MOUCLASS_DEVEXT_PENDING_IRP);

        if (!IsListEmpty(pendingQueue)) {
            // Fast path: complete a pending read IRP directly with our input
            PLIST_ENTRY entry = RemoveHeadList(pendingQueue);
            pendingIrp = CONTAINING_RECORD(entry, IRP, Tail.Overlay.ListEntry);

            // Atomically clear the cancel routine — if it's already NULL,
            // the IRP is being cancelled, put it back and fall through to queue path
            PDRIVER_CANCEL oldCancelRoutine = IoSetCancelRoutine(pendingIrp, NULL);

            if (oldCancelRoutine == NULL) {
                InsertHeadList(pendingQueue, entry);
                pendingIrp = NULL;
            }
            else {
                PIO_STACK_LOCATION irpStack = IoGetCurrentIrpStackLocation(pendingIrp);
                ULONG bufferSize = irpStack->Parameters.Read.Length;

                bytesToCopy = sizeof(MOUSE_INPUT_DATA);
                if (bytesToCopy > bufferSize) {
                    bytesToCopy = bufferSize;
                }

                // Get the IRP's output buffer (MDL-mapped or system buffer)
                PVOID irpBuffer = NULL;
                if (pendingIrp->MdlAddress != NULL) {
                    irpBuffer = MmGetSystemAddressForMdlSafe(pendingIrp->MdlAddress, NormalPagePriority);
                }
                if (irpBuffer == NULL) {
                    irpBuffer = pendingIrp->AssociatedIrp.SystemBuffer;
                }

                if (irpBuffer && bytesToCopy >= sizeof(MOUSE_INPUT_DATA)) {
                    RtlCopyMemory(irpBuffer, &inputData, sizeof(MOUSE_INPUT_DATA));
                }
                else {
                    bytesToCopy = 0;
                }
            }
        }

        // Slow path: no pending IRP, write directly into the circular buffer
        if (pendingIrp == NULL) {
            PVOID queueBase = *(PVOID*)(devExt + MOUCLASS_DEVEXT_DATA_QUEUE_BASE);
            PVOID writePtr = *(PVOID*)(devExt + MOUCLASS_DEVEXT_WRITE_POINTER);
            ULONG queueSize = *(PULONG)(devExt + MOUCLASS_DEVEXT_QUEUE_SIZE);
            PULONG inputCount = (PULONG)(devExt + MOUCLASS_DEVEXT_INPUT_COUNT);

            if (!queueBase || !writePtr || queueSize == 0) {
                KeReleaseSpinLock(spinLock, oldIrql);
                return STATUS_INVALID_DEVICE_STATE;
            }

            ULONG currentCount = *inputCount;
            ULONG maxEntries = queueSize / sizeof(MOUSE_INPUT_DATA);

            if (currentCount >= maxEntries - 1) {
                KeReleaseSpinLock(spinLock, oldIrql);
                return STATUS_INSUFFICIENT_RESOURCES;
            }

            // Write the input data at the current write pointer
            PMOUSE_INPUT_DATA bufEntry = (PMOUSE_INPUT_DATA)writePtr;
            *bufEntry = inputData;

            // Advance the write pointer, wrapping around if we hit the end
            PVOID newWritePtr = (PUCHAR)writePtr + sizeof(MOUSE_INPUT_DATA);
            PVOID bufferEnd = (PUCHAR)queueBase + queueSize;

            if (newWritePtr >= bufferEnd) {
                newWritePtr = queueBase;
            }

            *(PVOID*)(devExt + MOUCLASS_DEVEXT_WRITE_POINTER) = newWritePtr;
            (*inputCount)++;
        }

        KeReleaseSpinLock(spinLock, oldIrql);

        // Complete the pending IRP outside the spinlock (lower IRQL)
        if (pendingIrp != NULL) {
            pendingIrp->IoStatus.Status = STATUS_SUCCESS;
            pendingIrp->IoStatus.Information = bytesToCopy;
            IoCompleteRequest(pendingIrp, IO_MOUSE_INCREMENT);
        }

        return STATUS_SUCCESS;
    }

} // namespace Mouse
