"""
Root conftest.py — adds the project root to sys.path so all modules are
importable without installing the package.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
