#!/usr/local/bin/python3
"""Round 5 acceptance: rapid switching, readable names, isolated real shell."""
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.check_daily_hold_shell import main

if __name__=='__main__':raise SystemExit(main(stem='evolve2-5',browser_script='check_daily_research_shell.mjs'))
