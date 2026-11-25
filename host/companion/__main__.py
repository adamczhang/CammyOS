"""Run the offline demo: ``python3 host/companion --help``. Simulator only."""
from pathlib import Path
import sys

HOST = str(Path(__file__).resolve().parent.parent)
if HOST not in sys.path:
    sys.path.insert(0, HOST)

from companion.demo import main  # noqa: E402

sys.exit(main())
