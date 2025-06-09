#!/usr/bin/env python3
"""
Simple run script for the Aleva application.
Usage: python run.py
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

from aleva.main_window import main

if __name__ == "__main__":
    main() 