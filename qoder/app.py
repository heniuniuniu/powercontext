"""兼容入口：与 `python main.py`、`python server/search.py` 完全等价。

    python qoder/app.py              # 完整自检并退出，打印 SELFCHECK PASS
    python qoder/app.py --serve      # 常驻站点 + API 服务

只需 Python 3.9+ 标准库，零第三方依赖。
"""
from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
ENTRY = HERE / "server" / "search.py"
sys.argv = [str(ENTRY), *sys.argv[1:]]
runpy.run_path(str(ENTRY), run_name="__main__")
