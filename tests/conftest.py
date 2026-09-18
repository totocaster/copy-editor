import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="write-tests-")
os.environ["WRITE_DB"] = str(Path(_tmp) / "test.db")
