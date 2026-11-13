#pragma once

#include "imgui.h"

namespace colors {

    inline ImVec4 accent_color = ImColor(138, 92, 246);

    namespace menu {

        inline ImVec4 window_bg = ImColor(11, 11, 16);
        inline ImVec4 border = ImColor(28, 28, 40, 255);
        inline ImVec4 watermark_bg = ImColor(14, 14, 20, 255);
        inline ImVec4 watermark_border = ImColor(28, 28, 40, 255);
        inline ImVec4 watermark_filled = ImColor(20, 20, 28);

    }

    namespace tabs {

        inline ImVec4 text_active = ImColor(242, 240, 252);
        inline ImVec4 text_hovered = ImColor(185, 180, 205);
        inline ImVec4 text_inactive = ImColor(110, 106, 130);

    }

    namespace child {

        inline ImVec4 child_background = ImColor(16, 16, 23);
        inline ImVec4 child_top = ImColor(20, 20, 29);
        inline float child_rounding = 8.f;

    }

    namespace checkbox {

        inline ImVec4 text_active = ImColor(240, 238, 250);
        inline ImVec4 text_hovered = ImColor(190, 185, 210);
        inline ImVec4 text_inactive = ImColor(132, 128, 152);

        inline ImVec4 checkbox_bg_active = ImColor(36, 28, 58);
        inline ImVec4 checkbox_bg_inactive = ImColor(24, 24, 34);

        inline ImVec4 circle_inactive = ImColor(78, 75, 96);

    }

    namespace slider {

        inline ImVec4 text_active = ImColor(240, 238, 250);
        inline ImVec4 text_hovered = ImColor(190, 185, 210);
        inline ImVec4 text_inactive = ImColor(145, 140, 165);
        inline ImVec4 slider_inactive = ImColor(24, 24, 35);

    }

    namespace combo {


        inline ImVec4 text_active = ImColor(240, 238, 250);
        inline ImVec4 text_hovered = ImColor(190, 185, 210);
        inline ImVec4 text_inactive = ImColor(132, 128, 152);
        inline ImVec4 combo_bg = ImColor(21, 21, 31);

    }

    namespace color_picker {

        inline ImVec4 picker_bg = ImColor(21, 21, 31);

    }

    namespace binder {

        inline ImVec4 binder_bg = ImColor(21, 21, 31);
        inline ImVec4 text_active = ImColor(240, 238, 250);
        inline ImVec4 text_hovered = ImColor(190, 185, 210);
        inline ImVec4 text_inactive = ImColor(132, 128, 152);
        inline ImVec4 line = ImColor(36, 36, 50);
        inline ImVec4 image_active = ImColor(240, 238, 250);
        inline ImVec4 image_hovered = ImColor(190, 185, 210);
        inline ImVec4 image_inactive = ImColor(132, 128, 152);

    }

    namespace button {

        inline ImVec4 button_bg = ImColor(22, 22, 32);
        inline ImVec4 text_active = ImColor(245, 242, 255);
        inline ImVec4 text_inactive = ImColor(175, 170, 195);
    }

    namespace input {

        inline ImVec4 input_bg = ImColor(21, 21, 31);
        inline ImVec4 input_image = ImColor(47, 47, 62);
        inline ImVec4 text_active = ImColor(240, 238, 250);
        inline ImVec4 text_hovered = ImColor(190, 185, 210);
        inline ImVec4 text_inactive = ImColor(132, 128, 152);
    }

    namespace preview {

        inline ImVec4 rect = ImColor(36, 36, 50);
        inline ImVec4 name = ImColor(255, 255, 255);
        inline ImVec4 distance = ImColor(255, 255, 255);
        inline ImVec4 head = ImColor(255, 255, 255);
        inline ImVec4 dice = ImColor(255, 255, 255);

    }


}

#ifndef SETTINGS_CHECKBOX_ROUNDING_GUARD
#define SETTINGS_CHECKBOX_ROUNDING_GUARD
namespace settings {
    inline float checkbox_rounding = 100.f;
}
#endif
