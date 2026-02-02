"""
Pytest configuration for RXinDexer tests.

This file ensures that the electrumx modules can be imported correctly
during test execution.
"""

import sys
import os

# Add the project root directory to Python path
# This ensures that electrumx.lib and other modules can be imported
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Verify that critical modules are available
def pytest_configure(config):
    """Called after command line options have been parsed."""
    try:
        import electrumx.lib
        print("✅ electrumx.lib is available")
    except ImportError as e:
        print(f"❌ electrumx.lib not available: {e}")
        print(f"Project root in path: {project_root}")
        print(f"Current Python path: {sys.path[:3]}...")  # Show first few entries
