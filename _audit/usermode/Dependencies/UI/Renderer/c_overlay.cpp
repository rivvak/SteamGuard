#pragma once
#include "c_overlay.h"
#include "../Fonts/ubuntu_bold.h"
#include "../Fonts/ubuntu_medium.h"
#include "../Fonts/ubunutu_regular.h"
#include "../Fonts/Extras/icons.h"
#include <d3d11.h>

#define MYCLASSNAME    _("Microsoft (x86)")
#define MYWINDOWNAME   _("Microsoft (x86)")

using Overlay::DX11Overlay;

WNDCLASSEX DX11Overlay::wcex = { 0 };
MARGINS DX11Overlay::Margin = { -1 };
HINSTANCE DX11Overlay::InjectedModule;

HWND DX11Overlay::D11Overlay;
RECT DX11Overlay::Rect;
INT DX11Overlay::width;
INT DX11Overlay::height;
INT DX11Overlay::widthD2;
INT DX11Overlay::heightD2;
extern ImFont* ico_0;
extern ImFont* ico_1;
static LPDIRECT3D9              g_pD3D = NULL;
static LPDIRECT3DDEVICE9        g_pd3dDevice = NULL;

inline ImFont* ubu_0;
extern ImFont* ubu_2;

extern ImFont* ubu_child;


auto timeOld = std::chrono::high_resolution_clock::now();
auto timeNow = std::chrono::high_resolution_clock::now();
auto OverlayfpsClock = std::chrono::high_resolution_clock::now();

ID3D11Device* DX11Overlay::pDevice = nullptr;
ID3D11DeviceContext* DX11Overlay::PDeviceContext = nullptr;
IDXGISwapChain* DX11Overlay::pSwapChain = nullptr;
ID3D11RenderTargetView* DX11Overlay::pRenderTarget = nullptr;

extern LRESULT ImGui_ImplWin32_WndProcHandler(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

// ---- Tab ownership while the menu is open ----------------------------------
// The overlay window is click-through and never gets keyboard focus, so the
// game reads Tab straight from the OS — a WndProc swallow can't stop that.
// This low-level hook can: while the menu is up it eats Tab (downs and the
// matching up, auto-repeat included) so Fortnite's inventory stays closed,
// and the same press closes the menu. Everything else passes through;
// Alt+Tab arrives as WM_SYSKEY* and is never touched.
// The hook thread must pump messages — MainLoop's PeekMessage does.
static HHOOK g_tab_hook = nullptr;

static LRESULT CALLBACK TabHookProc(int nCode, WPARAM wParam, LPARAM lParam)
{
	if (nCode == HC_ACTION) {
		KBDLLHOOKSTRUCT* info = reinterpret_cast<KBDLLHOOKSTRUCT*>(lParam);
		if (info->vkCode == VK_TAB) {
			static bool s_tab_eaten = false; // we own this press until key-up
			if (wParam == WM_KEYDOWN) {
				if (C_Overlay::c_show_menu) {
					s_tab_eaten = true;
					C_Overlay::c_show_menu = false; // hook owns "close menu"
					return 1;                       // game never sees this Tab
				}
			}
			else if (wParam == WM_KEYUP && s_tab_eaten) {
				s_tab_eaten = false;
				return 1; // eat the matching key-up so the pair stays invisible
			}
		}
	}
	return CallNextHookEx(g_tab_hook, nCode, wParam, lParam);
}

static void InstallTabHook()
{
	if (!g_tab_hook)
		g_tab_hook = SetWindowsHookExW(WH_KEYBOARD_LL, TabHookProc, GetModuleHandleW(nullptr), 0);
}

LRESULT CALLBACK WndProc(HWND hWnd, UINT pMsg, WPARAM wParam, LPARAM lParam) {

	if (ImGui_ImplWin32_WndProcHandler(hWnd, pMsg, wParam, lParam) && C_Overlay::c_show_menu)
		return TRUE;

	return DefWindowProc(hWnd, pMsg, wParam, lParam);
}

VOID DX11Overlay::RenderBlur(HWND hwnd)
{

		struct ACCENTPOLICY
		{
			int na;
			int nf;
			int nc;
			int nA;
		};
		struct WINCOMPATTRDATA
		{
			int na;
			PVOID pd;
			ULONG ul;
		};

		// Cache the function pointer — user32.dll is always loaded, no need to LoadLibrary/FreeLibrary each call
		typedef BOOL(WINAPI* pSetWindowCompositionAttribute)(HWND, WINCOMPATTRDATA*);
		static pSetWindowCompositionAttribute SetWindowCompositionAttribute = nullptr;
		static bool resolved = false;
		if (!resolved) {
			resolved = true;
			HMODULE hm = GetModuleHandleA(_("user32.dll"));
			if (hm)
				SetWindowCompositionAttribute = (pSetWindowCompositionAttribute)GetProcAddress(hm, _("SetWindowCompositionAttribute"));
		}

		if (SetWindowCompositionAttribute)
		{
			ACCENTPOLICY policy = { 3, 0, 0, 0 }; // and even works 4,0,155,0 (Acrylic blur)
			WINCOMPATTRDATA data = { 19, &policy, sizeof(ACCENTPOLICY) };
			SetWindowCompositionAttribute(hwnd, &data);
		}
}

// Initialize ImGui
BOOL DX11Overlay::InitImGui() {

	ImGui::CreateContext();
	ImGuiIO& io = ImGui::GetIO();
	ImFontConfig fontConfig;
	fontConfig.RasterizerMultiply = 1.4f;

	io.Fonts->AddFontFromMemoryTTF(&khait, sizeof khait, 20, NULL, io.Fonts->GetGlyphRangesCyrillic());

	// Ubuntu Medium - ESP text font (clean, readable at small sizes)
	extern ImFont* esp_font;
	esp_font = io.Fonts->AddFontFromMemoryTTF(&ubuntu_0, sizeof ubuntu_0, 13, NULL, io.Fonts->GetGlyphRangesCyrillic());

	// Ubuntu Bold - menu/child text
	ubu_0 = io.Fonts->AddFontFromMemoryTTF(&ubuntu_1, sizeof ubuntu_1, 18, NULL, io.Fonts->GetGlyphRangesCyrillic());

	ImVec4 clear_color = ImVec4(0.f, 0.f, 0.f, 1.00f);

	ImGui::StyleColorsDark();
	ImGuiStyle& s = ImGui::GetStyle();

    s.Colors[ImGuiCol_WindowBg] = ImColor(165, 175, 185, 100);
    s.Colors[ImGuiCol_ChildBg] = ImColor(22, 21, 26, 255);
    s.Colors[ImGuiCol_PopupBg] = ImColor(17, 16, 21, 255);
    s.Colors[ImGuiCol_TextDisabled] = ImColor(66, 65, 70, 255);
    s.Colors[ImGuiCol_Border] = ImColor(14, 13, 19, 0);
    s.Colors[ImGuiCol_TextSelectedBg] = ImColor(35, 32, 43, 160);

    s.WindowBorderSize = 0;
    s.WindowPadding = ImVec2(0, 0);
    s.ChildRounding = 7;
    s.PopupRounding = 5;
    s.PopupBorderSize = 0;
    s.WindowRounding = 14.f;
    s.FrameBorderSize = 0.0f;
    s.FrameRounding = 4.f;
    s.ScrollbarSize = 2.f;
    s.FramePadding = ImVec2(6, 3);
    s.ItemInnerSpacing = ImVec2(10, 0);
    s.ItemSpacing = ImVec2(0, 10);

	// Initialize ImGui for Win32 and DX11
	if (ImGui_ImplWin32_Init(D11Overlay)) {
		if (ImGui_ImplDX11_Init(pDevice, PDeviceContext)) {
			InstallTabHook(); // own Tab while the menu is open (see TabHookProc)
			return TRUE;
		}
	}
	return FALSE;
}

// Function to create the overlay window
BOOL DX11Overlay::Hijack() {
	wcex.cbSize = sizeof(wcex);
	wcex.lpfnWndProc = WndProc;
	wcex.hInstance = InjectedModule;
	wcex.lpszClassName = MYCLASSNAME;

	if (!RegisterClassExA(&wcex)) {
		return FALSE;
	}

	//NOT GOOD. This needs to be changes like the entire overlay process needs to be changes. Just create a window and "remove" all flags or use task manager
	D11Overlay = CreateWindowExA(NULL, MYCLASSNAME, MYWINDOWNAME, WS_POPUP | WS_VISIBLE, 0, 0, width, height, 0, 0, InjectedModule, 0);
	if (!D11Overlay) {
		return FALSE;
	}

	SetWindowLong(D11Overlay, GWL_EXSTYLE, WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_TRANSPARENT);
	DwmExtendFrameIntoClientArea(D11Overlay, &Margin);

	// Stream-proof: exclude overlay from screen capture (OBS, Discord, etc.)
	// WDA_EXCLUDEFROMCAPTURE = 0x00000011 (Win10 2004+)
	if (C_Exploits::c_stream_proof) {
		SetWindowDisplayAffinity(D11Overlay, 0x00000011);
	}

	ShowWindow(D11Overlay, SW_SHOW);
	UpdateWindow(D11Overlay);

	SetWindowPos(D11Overlay, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED);

	return TRUE;
}

BOOL DX11Overlay::CreateRenderTarget() {
	ID3D11Texture2D* pBackBuffer = NULL;
	HRESULT hr = pSwapChain->GetBuffer(0, __uuidof(ID3D11Texture2D), (LPVOID*)&pBackBuffer);
	if (FAILED(hr)) {
		return FALSE;
	}

	hr = pDevice->CreateRenderTargetView(pBackBuffer, NULL, &pRenderTarget);
	pBackBuffer->Release();

	if (FAILED(hr)) {
		return FALSE;
	}

	return TRUE;
}

inline ImFont* icons;
inline ImFont* font_main;
ImFont* esp_font = nullptr;

// Function to create the Direct3D 11 swap chain
BOOL DX11Overlay::CreateSwapchain() {
	// Swap chain description
	DXGI_SWAP_CHAIN_DESC swapChainDesc = {};
	swapChainDesc.BufferCount = 1; // Single back buffer
	swapChainDesc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM; // 32-bit color
	swapChainDesc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT; // Render target usage
	swapChainDesc.OutputWindow = D11Overlay; // Overlay window handle
	swapChainDesc.SampleDesc.Count = 1; // No multi-sampling
	swapChainDesc.Windowed = TRUE; // Start in windowed mode
	swapChainDesc.SwapEffect = DXGI_SWAP_EFFECT_DISCARD; // Discard old frames
	swapChainDesc.Flags = DXGI_SWAP_CHAIN_FLAG_ALLOW_MODE_SWITCH; // Allow fullscreen mode switching

	// Feature levels
	D3D_FEATURE_LEVEL featureLevels[] = {
		D3D_FEATURE_LEVEL_11_0,
		D3D_FEATURE_LEVEL_10_1,
		D3D_FEATURE_LEVEL_10_0
	};
	D3D_FEATURE_LEVEL featureLevel;

	// Create the device and swap chain
	HRESULT hr = D3D11CreateDeviceAndSwapChain(
		NULL,
		D3D_DRIVER_TYPE_HARDWARE,
		NULL,
		D3D11_CREATE_DEVICE_BGRA_SUPPORT,
		featureLevels,
		_countof(featureLevels),
		D3D11_SDK_VERSION,
		&swapChainDesc,
		&pSwapChain,
		&pDevice,
		&featureLevel,
		&PDeviceContext
	);

	if (FAILED(hr)) {
		return FALSE;
	}

	// Create the render target
	if (!CreateRenderTarget()) {
		return FALSE;
	}

	// Set the render target
	PDeviceContext->OMSetRenderTargets(1, &pRenderTarget, NULL);

	return TRUE;
}

void DX11Overlay::ToggleFullscreen() {
	SPOOF_FUNC;

	BOOL isFullscreen;
	pSwapChain->GetFullscreenState(&isFullscreen, nullptr);
	pSwapChain->SetFullscreenState(!isFullscreen, nullptr);

	if (!isFullscreen) {
		// Now in fullscreen mode
		// Update overlay window size to match the game window's resolution
		GetWindowRect(GetForegroundWindow(), &Rect);
		SetWindowPos(D11Overlay, HWND_TOPMOST, Rect.left, Rect.top,
			Rect.right - Rect.left, Rect.bottom - Rect.top,
			SWP_NOOWNERZORDER | SWP_FRAMECHANGED);
	}
	else {
		// Now in windowed mode
		// Restore the original window size
		SetWindowPos(D11Overlay, HWND_TOPMOST, 0, 0, width, height, SWP_NOMOVE | SWP_NOSIZE);
	}
}

// Function to check if the GameWindow handle is valid
VOID DX11Overlay::ChechWindow(HWND GameWindow) {

	if (!GameWindow) TerminateProcess(GetCurrentProcess(), 3);
}

// Function to get the position and size of the GameWindow
VOID DX11Overlay::GetWindow(HWND GameWindow) {
	
	if (GetWindowRect(GameWindow, &Rect)) {
		width = Rect.right - Rect.left;
		height = Rect.bottom - Rect.top;

		widthD2 = width / 2;
		heightD2 = height / 2;
	}
}

// Function to initialize the overlay and associated components
BOOL DX11Overlay::InitOverlay() {
	
	if (!Hijack()) {
		std::cout << "nr1";
		return FALSE;
	}
	if (!CreateSwapchain()) {
		std::cout << "nr2";
		return FALSE;
	}
	if (!InitImGui()) {
		std::cout << "nr3";
		return FALSE;
	}
	return TRUE;
}

// Function to clean up and present the overlay
BOOL DX11Overlay::Cleanup() {

	static float vCleanColor[4]{ 0, 0, 0, 0 };

	// Render ImGui (Render() calls EndFrame() internally)
	ImGui::Render();

	// Clear the render target view with the specified clean color
	PDeviceContext->ClearRenderTargetView(pRenderTarget, vCleanColor);

	// Render ImGui draw data
	ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());

	// Present the swap chain
	HRESULT pSent = pSwapChain->Present(1, 0);
	if (SUCCEEDED(pSent))
		return TRUE;
	return FALSE;
}

// Function to change the clickability of the overlay window
bool clickable = false;
void change_click(bool canclick) {
	long style = ::GetWindowLong(DX11Overlay::D11Overlay, GWL_EXSTYLE);
	if (canclick)
	{
		style &= ~WS_EX_TRANSPARENT;
		SetWindowLong(DX11Overlay::D11Overlay, GWL_EXSTYLE, style);
		SetFocus(DX11Overlay::D11Overlay);
		clickable = true;
	}
	else
	{
		style |= WS_EX_TRANSPARENT;
		SetWindowLong(DX11Overlay::D11Overlay, GWL_EXSTYLE, style);
		clickable = false;
	}
}

void UpdateOverlayPosition() {
	// Cache the game window handle — FindWindow is an expensive O(n) enumeration
	static HWND gameWindow = NULL;
	if (!gameWindow || !IsWindow(gameWindow)) {
		gameWindow = FindWindow(NULL, "Fortnite  ");
	}

	RECT gameRect;
	if (gameWindow && GetWindowRect(gameWindow, &gameRect)) {
		static RECT lastRect = {};
		int w = gameRect.right - gameRect.left;
		int h = gameRect.bottom - gameRect.top;

		if (memcmp(&lastRect, &gameRect, sizeof(RECT)) != 0) {
			lastRect = gameRect;
			SetWindowPos(DX11Overlay::D11Overlay, HWND_TOPMOST, gameRect.left, gameRect.top,
				w, h, SWP_NOOWNERZORDER | SWP_FRAMECHANGED);

			// Sync screen dimensions to actual game window so W2S uses correct center
			C_Overlay::c_screen_width = w;
			C_Overlay::c_screen_height = h;
			C_Overlay::c_center_x = w / 2;
			C_Overlay::c_center_y = h / 2;
		}
	}
}

// Function to run the main overlay loop
BOOL DX11Overlay::MainLoop(std::function<VOID()> DrawLoop) {
	MSG Msg;
	static float clearColor[4]{ 0, 0, 0, 0 };

	if (PeekMessageA(&Msg, NULL, 0, 0, PM_REMOVE)) {
		TranslateMessage(&Msg);
		DispatchMessageA(&Msg);
	}

	// Screen dimensions are updated in UpdateOverlayPosition() from the game window

	ImGuiIO& io = ImGui::GetIO();
	io.IniFilename = NULL;

	// Start a new frame for ImGui
	ImGui_ImplDX11_NewFrame();
	ImGui_ImplWin32_NewFrame();
	ImGui::NewFrame();
	{
		// Execute the DrawLoop function provided by the user
		DrawLoop();
		c_draw_fov();

		// Handle fullscreen state (once per frame, not 3x)
		if (C_Overlay::c_fullscreen) {
			pSwapChain->SetFullscreenState(TRUE, NULL);
		}

		// Update overlay position once per frame
		UpdateOverlayPosition();

		// Open menu on TAB when menu is currently closed.
		// (Closing while open is handled cleanly by TabHookProc so the game never sees Tab).
		if (!C_Overlay::c_show_menu && (GetAsyncKeyState(VK_TAB) & 1))
		{
			C_Overlay::c_show_menu = true;
		}

		// Stream-proof toggle (runtime)
		{
			static bool last_stream_proof = C_Exploits::c_stream_proof;
			if (C_Exploits::c_stream_proof != last_stream_proof) {
				SetWindowDisplayAffinity(D11Overlay, C_Exploits::c_stream_proof ? 0x00000011 : 0x00000000);
				last_stream_proof = C_Exploits::c_stream_proof;
			}
		}

		// If the menu is shown, make the overlay window clickable
		if (C_Overlay::c_show_menu) {
			if (!clickable)
				change_click(true);

			// Draw the menu content
			draw_menu();
		}

		// If the menu is hidden, make the overlay window not clickable
		else {
			if (clickable)
				change_click(false);
		}
	}

	// Render ImGui draw data and present the swap chain
	// (Render() calls EndFrame() internally — do NOT call EndFrame() separately)
	ImGui::Render();
	PDeviceContext->ClearRenderTargetView(pRenderTarget, clearColor);
	ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
	pSwapChain->Present(C_Overlay::c_enable_vsync, 0);

	// Return whether the message is not WM_QUIT, indicating the loop should continue
	return Msg.message != WM_QUIT;
}