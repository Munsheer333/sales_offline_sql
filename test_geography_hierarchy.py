import unittest
import json
import os

class TestGeographyHierarchy(unittest.TestCase):
    def test_geography_hierarchy_metadata(self):
        # Path to the generated metadata file
        base_dir = os.path.dirname(os.path.abspath(__file__))
        hierarchy_path = os.path.join(base_dir, "data", "geography_hierarchy.json")
        
        # Check that the file exists
        self.assertTrue(os.path.exists(hierarchy_path), f"Geography hierarchy metadata not found at {hierarchy_path}")
        
        # Load and parse the JSON file
        with open(hierarchy_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # Verify structure
        self.assertIn("hierarchy_levels", data)
        self.assertIn("root", data)
        
        # Verify levels
        expected_levels = ["country", "state", "region", "zone", "district"]
        self.assertEqual(data["hierarchy_levels"], expected_levels)
        
        # Verify root country details
        root = data["root"]
        self.assertEqual(root["name"], "INDIA")
        self.assertEqual(root["level"], "country")
        self.assertIn("states", root)
        
        # Verify nesting and data completeness
        states = root["states"]
        self.assertGreater(len(states), 0, "No states found in hierarchy metadata")
        
        # Check a specific state
        self.assertIn("ANDHRA PRADESH", states)
        ap = states["ANDHRA PRADESH"]
        self.assertEqual(ap["level"], "state")
        self.assertIn("regions", ap)
        
        ap_regions = ap["regions"]
        self.assertIn("SOUTH", ap_regions)
        south = ap_regions["SOUTH"]
        self.assertEqual(south["level"], "region")
        self.assertIn("zones", south)
        
        south_zones = south["zones"]
        self.assertIn("SCZ", south_zones)
        scz = south_zones["SCZ"]
        self.assertEqual(scz["level"], "zone")
        self.assertIn("districts", scz)
        
        scz_districts = scz["districts"]
        self.assertIn("CHITTOOR", scz_districts)
        
if __name__ == "__main__":
    unittest.main()
