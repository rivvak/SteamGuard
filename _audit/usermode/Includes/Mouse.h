#include <Windows.h>


namespace Mouse {
    static void SetCursorPosition(fvector2d vTarget) {
        std::srand(static_cast<unsigned>(std::time(nullptr)));
        int iMaxMistakePx = 0;

        fvector2d vScreenCenter(c_screen_width / 2, c_screen_height / 2);
        fvector2d vDestination(0, 0);
        int iSmoothFactor = c_smooth + 1;

        if (vTarget.x != 0)
        {
            int xMistake = -iMaxMistakePx + (std::rand() % (2 * iMaxMistakePx + 1));
            vDestination.x = (vTarget.x > vScreenCenter.x) ? -(vScreenCenter.x - vTarget.x + xMistake) : (vTarget.x - vScreenCenter.x + xMistake);
            vDestination.x /= iSmoothFactor;
            vDestination.x = (vDestination.x + vScreenCenter.x > c_screen_width) ? 0 : vDestination.x;
        }

        if (vTarget.y != 0)
        {
            int yMistake = -iMaxMistakePx + (std::rand() % (2 * iMaxMistakePx + 1));
            vDestination.y = (vTarget.y > vScreenCenter.y) ? -(vScreenCenter.y - vTarget.y + yMistake) : (vTarget.y - vScreenCenter.y + yMistake);
            vDestination.y /= iSmoothFactor;
            vDestination.y = (vDestination.y + vScreenCenter.y > c_screen_height) ? 0 : vDestination.y;
        }

        device_().MoveMouse(vDestination.x, vDestination.y, NULL);
    }

    static void LeftDown(int x = 0, int y = 0) {
        device_().MoveMouse(NULL, NULL, MOUSE_LEFT_BUTTON_DOWN);
    }

    static void LeftUp(int x = 0, int y = 0) {
        device_().MoveMouse(NULL, NULL, MOUSE_LEFT_BUTTON_UP);
    }
}