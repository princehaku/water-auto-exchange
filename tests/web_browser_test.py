"""Compatibility entry point for the single 3D web console browser checks."""
import sys
from aquarium_browser_test import main


if __name__ == '__main__':
    main(layout_only='--layout-only' in sys.argv)
