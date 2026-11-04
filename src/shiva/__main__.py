import sys

if "--clean-worker" in sys.argv[1:2]:  # frozen build: same exe doubles as the separate-process cleaning helper
    from .metadata.worker import main
else:
    from .gui import main

sys.exit(main())
