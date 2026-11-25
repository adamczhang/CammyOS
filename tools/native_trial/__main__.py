import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from native_trial.cli import main
else:
    from .cli import main

sys.exit(main())
