"""讓 tests 不用安裝成套件就能 import claude_chat（pytest 預設只把 tests/ 放進 sys.path）。
每個測試都拿自己的臨時 SQLite：run／事件／待答卡的落地絕不能寫進正式的 state.sqlite。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path_factory):
    # 放在獨立目錄，不放 tmp_path：有些測試會列舉自己的 tmp_path，不該看到資料庫檔
    from claude_chat import store
    store.init(tmp_path_factory.mktemp("store") / "state.sqlite")
