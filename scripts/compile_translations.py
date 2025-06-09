#!/usr/bin/env python3
"""
Script to compile Qt translation files from .ts to .qm format.
Requires PySide6 tools to be installed.
"""

import os
import subprocess
import sys
from pathlib import Path

def find_lrelease():
    """Find the lrelease executable"""
    # Try common locations for lrelease
    possible_paths = [
        'lrelease',  # In PATH
        'pyside6-lrelease',  # PySide6 version
    ]
    
    for path in possible_paths:
        try:
            subprocess.run([path, '-version'], capture_output=True, check=True)
            return path
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    
    # If not found in PATH, try to find it in Python site-packages
    try:
        import PySide6
        pyside6_path = Path(PySide6.__file__).parent
        possible_lrelease = pyside6_path / 'lrelease'
        if possible_lrelease.exists():
            return str(possible_lrelease)
        
        # Try with .exe extension on Windows
        possible_lrelease_exe = pyside6_path / 'lrelease.exe'
        if possible_lrelease_exe.exists():
            return str(possible_lrelease_exe)
            
    except ImportError:
        pass
    
    return None

def compile_translations():
    """Compile all .ts files to .qm files"""
    languages_dir = Path(__file__).parents[1] / 'src' / 'aleva' / 'languages'
    
    if not languages_dir.exists():
        print("Languages directory not found!")
        return False
    
    lrelease_path = find_lrelease()
    if not lrelease_path:
        print("lrelease tool not found!")
        print("Please install Qt tools or PySide6 development tools")
        return False
    
    print(f"Using lrelease: {lrelease_path}")
    
    ts_files = list(languages_dir.glob('*.ts'))
    if not ts_files:
        print("No .ts files found in languages directory!")
        return False
    
    success = True
    for ts_file in ts_files:
        qm_file = ts_file.with_suffix('.qm')
        print(f"Compiling {ts_file.name} -> {qm_file.name}")
        
        try:
            result = subprocess.run([
                lrelease_path, 
                str(ts_file), 
                '-qm', 
                str(qm_file)
            ], capture_output=True, text=True, check=True)
            
            if result.stdout:
                print(f"  {result.stdout.strip()}")
                
        except subprocess.CalledProcessError as e:
            print(f"  Error compiling {ts_file.name}: {e}")
            if e.stderr:
                print(f"  {e.stderr.strip()}")
            success = False
    
    return success

if __name__ == '__main__':
    if compile_translations():
        print("Translation compilation completed successfully!")
    else:
        print("Translation compilation failed!")
        sys.exit(1) 