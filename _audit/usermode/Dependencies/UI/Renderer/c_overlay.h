#pragma once
#include "../../../Includes/includes.hpp"
#include <functional>
#include "../Menu/menu.hpp"
#include "../../../Includes/Definitions/c_definitions.hpp"

namespace Overlay
{
	class DX11Overlay
	{
	private:
		
		static BOOL CreateSwapchain();
		static BOOL Hijack();
		static BOOL CreateRenderTarget();
		static BOOL InitImGui();
		static VOID ChechWindow(HWND GameWindow);
		static VOID RenderBlur(HWND hwnd);
		static BOOL Cleanup();
		static void ToggleFullscreen();
	public:
		static Overlay::DX11Overlay& Instance();
		static BOOL MainLoop(std::function<VOID()> DrawLoop);
		static VOID Move_Window();
		static WNDCLASSEX wcex;
		static MARGINS Margin;


		static ID3D11DeviceContext* PDeviceContext;
		static IDXGISwapChain* pSwapChain;
		static ID3D11RenderTargetView* pRenderTarget;
		//static Initialize::STATUS InitOverlay();
		static BOOL InitOverlay();
		static VOID GetWindow(HWND GameWindow);
		//static BOOL IsKeyPressed(int VK_CODE, bool ShowMenu = false);

		static HINSTANCE InjectedModule;
		static ID3D11Device* pDevice;
		static RECT Rect;

		static HWND D11Overlay;

		static INT width;
		static INT height;
		static INT widthD2;
		static INT heightD2;
	};
}
