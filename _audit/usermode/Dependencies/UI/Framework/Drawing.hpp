#include "../Drawing/imgui.h"

void draw_static(int X, int Y, int W, int H, const ImColor color, int thickness)
{
	SPOOF_FUNC;
	auto dl = ImGui::GetBackgroundDrawList();

	float lineW = (float)W;
	float lineH = (float)H;

	ImColor blackColor = ImColor(0, 0, 0, 255);
	dl->AddLine(ImVec2(X, Y), ImVec2(X, Y + lineH), blackColor, 1);
	dl->AddLine(ImVec2(X, Y), ImVec2(X + lineW, Y), blackColor, 1);
	dl->AddLine(ImVec2(X + W - lineW, Y), ImVec2(X + W, Y), blackColor, 1);
	dl->AddLine(ImVec2(X + W, Y), ImVec2(X + W, Y + lineH), blackColor, 1);
	dl->AddLine(ImVec2(X, Y + H - lineH), ImVec2(X, Y + H), blackColor, 1);
	dl->AddLine(ImVec2(X, Y + H), ImVec2(X + lineW, Y + H), blackColor, 1);
	dl->AddLine(ImVec2(X + W - lineW, Y + H), ImVec2(X + W, Y + H), blackColor, 1);
	dl->AddLine(ImVec2(X + W, Y + H - lineH), ImVec2(X + W, Y + H), blackColor, 1);

	float offset = thickness / 2.0f;

	dl->AddLine(ImVec2(X + offset, Y + offset), ImVec2(X + offset, Y + lineH - offset), color, thickness);
	dl->AddLine(ImVec2(X + offset, Y + offset), ImVec2(X + lineW - offset, Y + offset), color, thickness);
	dl->AddLine(ImVec2(X + W - lineW + offset, Y + offset), ImVec2(X + W - offset, Y + offset), color, thickness);
	dl->AddLine(ImVec2(X + W - offset, Y + offset), ImVec2(X + W - offset, Y + lineH - offset), color, thickness);
	dl->AddLine(ImVec2(X + offset, Y + H - lineH + offset), ImVec2(X + offset, Y + H - offset), color, thickness);
	dl->AddLine(ImVec2(X + offset, Y + H - offset), ImVec2(X + lineW - offset, Y + H - offset), color, thickness);
	dl->AddLine(ImVec2(X + W - lineW + offset, Y + H - offset), ImVec2(X + W - offset, Y + H - offset), color, thickness);
	dl->AddLine(ImVec2(X + W - offset, Y + H - lineH + offset), ImVec2(X + W - offset, Y + H - offset), color, thickness);
}



void cornered(int X, int Y, int W, int H, int thickness, ImColor color) {
	SPOOF_FUNC;
	auto dl = ImGui::GetBackgroundDrawList();
	float lineW = (W / 5);
	float lineH = (H / 6);
	float lineT = 1;
	ImU32 black = IM_COL32(1, 1, 1, 255);

	//outline
	dl->AddLine(ImVec2(X - lineT, Y - lineT), ImVec2(X + lineW, Y - lineT), black, thickness);
	dl->AddLine(ImVec2(X - lineT, Y - lineT), ImVec2(X - lineT, Y + lineH), black, thickness);
	dl->AddLine(ImVec2(X - lineT, Y + H - lineH), ImVec2(X - lineT, Y + H + lineT), black, thickness);
	dl->AddLine(ImVec2(X - lineT, Y + H + lineT), ImVec2(X + lineW, Y + H + lineT), black, thickness);
	dl->AddLine(ImVec2(X + W - lineW, Y - lineT), ImVec2(X + W + lineT, Y - lineT), black, thickness);
	dl->AddLine(ImVec2(X + W + lineT, Y - lineT), ImVec2(X + W + lineT, Y + lineH), black, thickness);
	dl->AddLine(ImVec2(X + W + lineT, Y + H - lineH), ImVec2(X + W + lineT, Y + H + lineT), black, thickness);
	dl->AddLine(ImVec2(X + W - lineW, Y + H + lineT), ImVec2(X + W + lineT, Y + H + lineT), black, thickness);

	//inline
	dl->AddLine(ImVec2(X, Y), ImVec2(X, Y + lineH), color, thickness);
	dl->AddLine(ImVec2(X, Y), ImVec2(X + lineW, Y), color, thickness);
	dl->AddLine(ImVec2(X + W - lineW, Y), ImVec2(X + W, Y), color, thickness);
	dl->AddLine(ImVec2(X + W, Y), ImVec2(X + W, Y + lineH), color, thickness);
	dl->AddLine(ImVec2(X, Y + H - lineH), ImVec2(X, Y + H), color, thickness);
	dl->AddLine(ImVec2(X, Y + H), ImVec2(X + lineW, Y + H), color, thickness);
	dl->AddLine(ImVec2(X + W - lineW, Y + H), ImVec2(X + W, Y + H), color, thickness);
	dl->AddLine(ImVec2(X + W, Y + H - lineH), ImVec2(X + W, Y + H), color, thickness);
}



void outlined_text(ImDrawList* drawlist, int x, int y, ImColor Color, const char* text)
{
	SPOOF_FUNC;

	ImVec2 offsets[] = { {-1, 0}, {1, 0}, {0, -1}, {0, 1} };

	for (const auto& offset : offsets)
	{
		drawlist->AddText(ImVec2(x + offset.x, y + offset.y), ImColor(0, 0, 0, 255), text);
	}

	drawlist->AddText(ImVec2(x, y), Color, text);
}

inline std::wstring MBytesToWString(const char* lpcszString)
{
	SPOOF_FUNC;

	int len = strlen(lpcszString);
	int unicodeLen = ::MultiByteToWideChar(CP_ACP, 0, lpcszString, -1, NULL, 0);
	wchar_t* pUnicode = new wchar_t[unicodeLen + 1];
	memset(pUnicode, 0, (unicodeLen + 1) * sizeof(wchar_t));
	::MultiByteToWideChar(CP_ACP, 0, lpcszString, -1, (LPWSTR)pUnicode, unicodeLen);
	std::wstring wString = (wchar_t*)pUnicode;
	delete[] pUnicode;
	return wString;
}
inline std::string WStringToUTF8(const wchar_t* lpwcszWString)
{
	SPOOF_FUNC;

	char* pElementText;
	int iTextLen = ::WideCharToMultiByte(CP_UTF8, 0, (LPWSTR)lpwcszWString, -1, NULL, 0, NULL, NULL);
	pElementText = new char[iTextLen + 1];
	memset((void*)pElementText, 0, (iTextLen + 1) * sizeof(char));
	::WideCharToMultiByte(CP_UTF8, 0, (LPWSTR)lpwcszWString, -1, pElementText, iTextLen, NULL, NULL);
	std::string strReturn(pElementText);
	delete[] pElementText;
	return strReturn;
}

inline void DrawString(float fontSize, int x, int y, float color[4], bool bCenter, bool stroke, const char* pText, ...)
{
	ImU32 converted_color = ImGui::ColorConvertFloat4ToU32(ImVec4(color[0], color[1], color[2], color[3]));
	va_list va_alist;
	char buf[1024] = { 0 };
	va_start(va_alist, pText);
	_vsnprintf_s(buf, sizeof(buf), pText, va_alist);
	va_end(va_alist);
	std::string text = WStringToUTF8(MBytesToWString(buf).c_str());
	if (bCenter)
	{
		ImVec2 textSize = ImGui::CalcTextSize(text.c_str());
		x = x - textSize.x / 2;  // fixed: was /4, should be /2 to center properly
		y = y - textSize.y;
	}
	// Cache draw list and font lookups (avoid repeated virtual calls per text draw)
	auto dl = ImGui::GetBackgroundDrawList();
	auto font = ImGui::GetFont();
	if (stroke)
	{
		static const ImU32 outlineCol = IM_COL32(1, 1, 1, 255);
		dl->AddText(font, fontSize, ImVec2(x, y - 1), outlineCol, text.c_str());
		dl->AddText(font, fontSize, ImVec2(x, y + 1), outlineCol, text.c_str());
		dl->AddText(font, fontSize, ImVec2(x - 1, y), outlineCol, text.c_str());
		dl->AddText(font, fontSize, ImVec2(x + 1, y), outlineCol, text.c_str());
	}
	dl->AddText(font, fontSize, ImVec2(x, y), converted_color, text.c_str());
}

 void draw_string(float fontSize, int x, int y, ImColor color, bool bCenter, bool stroke, std::string text)
{
	 SPOOF_FUNC;

	if (text.empty()) {
		return;
	}

	if (bCenter) {
		ImVec2 textSize = ImGui::CalcTextSize(text.c_str());
		x -= textSize.x / 2;
		y -= textSize.y / 2;
	}

	auto dl = ImGui::GetBackgroundDrawList();
	auto font = ImGui::GetFont();

	// Outline/stroke the text if required
	if (stroke) {
		static const ImU32 black_color = IM_COL32(0, 0, 0, 255);
		dl->AddText(font, fontSize, ImVec2(x + 1, y + 1), black_color, text.c_str());
		dl->AddText(font, fontSize, ImVec2(x - 1, y - 1), black_color, text.c_str());
		dl->AddText(font, fontSize, ImVec2(x + 1, y - 1), black_color, text.c_str());
		dl->AddText(font, fontSize, ImVec2(x - 1, y + 1), black_color, text.c_str());
	}

	dl->AddText(font, fontSize, ImVec2(x, y), color, text.c_str());
}

 void line(fvector ScreenPositionA, fvector ScreenPositionB, int Thickness, ImColor RenderColor, bool outline) {
	 SPOOF_FUNC;

	auto dl = ImGui::GetBackgroundDrawList();
	ImVec2 a(ScreenPositionA.x, ScreenPositionA.y);
	ImVec2 b(ScreenPositionB.x + 1, ScreenPositionB.y + 1);
	if (outline)
	{
		dl->AddLine(a, b, IM_COL32(0, 0, 0, 255), Thickness + 1.5f);
	}
	dl->AddLine(a, b, RenderColor, Thickness);
}

void filled(int x, int y, int w, int h, ImColor color1, ImColor color2)
{
	SPOOF_FUNC;

	ImDrawList* draw_list = ImGui::GetBackgroundDrawList();
	draw_list->AddRectFilledMultiColor(
		ImVec2(x, y), ImVec2(x + w, y + h),
		color1, color1, color2, color2
	);
}

void drawstatic_(const fvector2d head_box, float box_width, float box_height, const ImColor& viscolor,int c_outlined1) {
	SPOOF_FUNC;

	draw_static((head_box.x - (box_width / 2)), head_box.y, box_width, box_height, viscolor, c_outlined1);
}

void drawfilled_(const fvector2d head_box, float box_width, float box_height, const ImColor& filldecolor, const ImColor& filldecolor1) {
	SPOOF_FUNC;

	filled((head_box.x - (box_width / 2)), head_box.y, box_width, box_height, filldecolor, filldecolor1);
}

void drawcorner_(const fvector2d head_box, float box_width, float box_height, const ImColor& viscolor, int c_outlined1) {
	SPOOF_FUNC;

	cornered((head_box.x - (box_width / 2)), head_box.y, box_width, box_height, c_outlined1, viscolor);
}

void drawoutlined_(const std::string& text, const ImVec2& position, ImColor color, int offset_y) {
	SPOOF_FUNC;

	ImVec2 text_size = ImGui::CalcTextSize(text.c_str());
	outlined_text(ImGui::GetBackgroundDrawList(), position.x - (text_size.x / 2), position.y + offset_y, color, text.c_str());
}
