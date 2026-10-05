"""Put `server/` on the path so the tests import the module the way the app does."""

import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
