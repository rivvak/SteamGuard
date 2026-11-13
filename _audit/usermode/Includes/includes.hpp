#include <Windows.h>
#include <iostream>
#include <TlHelp32.h> // UD?
#include <cstdint>
#include <chrono>

#include <d3d11.h>
#include <dwmapi.h>

#include "../Dependencies/UI/Drawing/imgui.h"
#include "../Dependencies/UI/Backend/imgui_impl_dx11.h"
#include "../Dependencies/UI/Backend/imgui_impl_win32.h"
#include "../Dependencies/UI/Drawing/imgui_internal.h"

#include "../Includes/Definitions/xor.hpp"
#include "../Includes/Definitions/spoofer.h"
#include "../oxorany_include.h"
