#include <windows.h>
#include <iostream>
#include <memory>
#include <unordered_map>
#include <map>

namespace GameCache {

	class world_levels {
	private:
		// private members (if any)

	public:

		struct fortnite {
			static uint64_t start(int);

			enum type {
				not_used = 0,
				chest = 1,
				ammo = 2,
				floor = 3,
			};

			struct defs {
				int actorid;
				std::string fname;
				type actor;
			};

			static const std::unordered_map<std::string, type>& getactors() {
				static const std::unordered_map<std::string, type> actortype = {
					{"Tiered_Chest", type::chest},
					{"Tiered_Ammo", type::ammo},
					{"FortPickupAthena", type::floor }
				};

				return actortype;
			}

			static defs getdefines(int actorID);

		};

		static GameCache::world_levels& getInstance() {
			static GameCache::world_levels Instance;
			return Instance;
		}

		void getloc(); // Declare the function
	};

}



inline GameCache::world_levels& world_lvls()
{
	return GameCache::world_levels::getInstance();
}
