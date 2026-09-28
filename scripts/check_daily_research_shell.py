#!/usr/local/bin/python3
"""Round 4: reuse isolated shell lifecycle, keys removed, private DB backup."""
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.check_daily_hold_shell import main

if __name__=='__main__':raise SystemExit(main(stem='evolve2-4',browser_script='check_daily_research_shell.mjs'))
