import sys

if "--desktop" in sys.argv:
    sys.argv.remove("--desktop")
    from .desktop import main
else:
    from .webserver import main

main()
