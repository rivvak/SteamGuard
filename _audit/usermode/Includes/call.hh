#pragma once
#include "includes.hpp"


#define STATUS_SUCCESS   ((NTSTATUS)0x00000000L) 

namespace call {

	enum status : int32_t {
		successful_action = STATUS_SUCCESS,
		failed_hijack = 0,
		failed_intialization = 0,
		failed_communication = 0,
		failed_window = 0,
		failed_to_get_pid = 0,
		failed_to_get_image_base = 0,
		failed_to_initialize_hid_mouse = 0,
	};
}

#define call_success call::status::successful_action
#define call_failed_driver call::status::failed_communication
#define call_failed_overlay call::status::failed_hijack
#define call_failed_image call::status::failed_to_get_image_base
#define call_failed_mouse call::status::failed_to_initialize_hid_mouse