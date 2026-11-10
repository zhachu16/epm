"""PyInstaller's entry script for epm.exe; the program itself is epm.desktop."""

from epm.desktop import main

raise SystemExit(main())
