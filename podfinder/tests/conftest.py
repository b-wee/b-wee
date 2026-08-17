"""Test configuration: point the app at a throwaway database and disable
the background sync loop BEFORE any app module gets imported."""

import os
import tempfile

_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["PODFINDER_DB"] = f"sqlite:///{_db_file.name}"
os.environ["PODFINDER_AUTO_SYNC"] = "0"
