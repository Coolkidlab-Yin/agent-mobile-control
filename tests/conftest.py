"""讓 tests 不用安裝成套件就能 import claude_chat（pytest 預設只把 tests/ 放進 sys.path）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
