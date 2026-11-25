import os
import json
from config import DB_URL
from core.db_pool import pooled_connect

def generate_geography_hierarchy():
    conn = pooled_connect(DB_URL)
    
    # Query database for all unique combinations of geographic fields
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT statename, region_name, zone_name, distname
            FROM industry_performance
            WHERE statename IS NOT NULL 
              AND region_name IS NOT NULL 
              AND zone_name IS NOT NULL 
              AND distname IS NOT NULL
            ORDER BY statename, region_name, zone_name, distname
        """)
        rows = cur.fetchall()

    # Build the tree
    # Level hierarchy: Country -> State -> Region -> Zone -> District
    hierarchy = {
        "hierarchy_levels": ["country", "state", "region", "zone", "district"],
        "root": {
            "name": "INDIA",
            "level": "country",
            "states": {}
        }
    }
    
    states_dict = hierarchy["root"]["states"]
    
    for state, region, zone, district in rows:
        state = state.strip()
        region = region.strip()
        zone = zone.strip()
        district = district.strip()
        
        if state not in states_dict:
            states_dict[state] = {
                "name": state,
                "level": "state",
                "regions": {}
            }
            
        regions_dict = states_dict[state]["regions"]
        if region not in regions_dict:
            regions_dict[region] = {
                "name": region,
                "level": "region",
                "zones": {}
            }
            
        zones_dict = regions_dict[region]["zones"]
        if zone not in zones_dict:
            zones_dict[zone] = {
                "name": zone,
                "level": "zone",
                "districts": []
            }
            
        districts_list = zones_dict[zone]["districts"]
        if district not in districts_list:
            districts_list.append(district)

    # Save to data/geography_hierarchy.json
    output_path = "data/geography_hierarchy.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(hierarchy, f, indent=2)
        
    print(f"Geography hierarchy metadata generated at: {output_path}")
    print(f"Total States: {len(states_dict)}")
    total_regions = sum(len(s["regions"]) for s in states_dict.values())
    total_zones = sum(sum(len(r["zones"]) for r in s["regions"].values()) for s in states_dict.values())
    total_districts = sum(sum(sum(len(z["districts"]) for z in r["zones"].values()) for r in s["regions"].values()) for s in states_dict.values())
    print(f"Total Regions: {total_regions}")
    print(f"Total Zones: {total_zones}")
    print(f"Total Districts: {total_districts}")

if __name__ == "__main__":
    generate_geography_hierarchy()
