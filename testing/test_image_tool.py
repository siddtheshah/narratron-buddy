import os
import sys
# Add parent directory to path so imports work when run directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import yaml
from tools.image_tool import ImageTools

def main():
    print("Loading config...")
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.yaml'))
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    
    print("Initializing ImageTools...")
    image_tools = ImageTools(config)
    
    print("Attempting to create image...")
    try:
        result = image_tools.create_image("A futuristic city at sunset with flying cars", "Futuristic city metadata")
        print("Success! Result:")
        print(result)
    except Exception as e:
        print(f"Error creating image: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
