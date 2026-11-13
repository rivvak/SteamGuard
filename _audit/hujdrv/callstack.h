#pragma once
#ifdef _KERNEL_MODE
#include <ntddk.h>
#include <ntdef.h>
#else
#include <Windows.h>
#include <utility>
#endif
#include  <Intrin.h> 

/*
 *  Copyright 2022 Barracudach
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

 // === FAQ === documentation is available at https://github.com/Barracudach
//Supports 2 modes: kernelmode and usermode(x64)
//For kernel- disable Control Flow Guard (CFG) /guard:cf 
//usermode c++17 and above
//kernelmode c++14 and above



#define SPOOF_FUNC CallSpoofer::SpoofFunction spoof(_AddressOfReturnAddress());
#ifdef _KERNEL_MODE
#define SPOOF_CALL(ret_type,name) (CallSpoofer::SafeCall<ret_type,std::remove_reference_t<decltype(*name)>>(name))
#else
#define SPOOF_CALL(name) (CallSpoofer::SafeCall(name))
#endif


#define MAX_FUNC_BUFFERED 100
#define SHELLCODE_GENERATOR_SIZE 500

namespace CallSpoofer
{
#ifdef _KERNEL_MODE
	typedef unsigned __int64  uintptr_t, size_t;
#pragma region std::forward

#pragma endregion 

#else
	using namespace std;
#endif

}


namespace CallSpoofer
{
	class SpoofFunction
	{
	public:
		SpoofFunction(void* addr)
		{
			(void)addr;
		}
		~SpoofFunction() = default;
	};

#ifdef _KERNEL_MODE
	template<typename RetType, class Func>
#else
	template<class Func>
#endif
	class SafeCall
	{
		Func* funcPtr;

	public:
		SafeCall(Func* func) : funcPtr(func) {}

		template<typename... Args>
		__forceinline decltype(auto) operator()(Args&&... args)
		{
			return funcPtr( static_cast<Args&&>(args)... );
		}
	};
}