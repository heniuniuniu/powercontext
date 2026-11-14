"""仓库根目录兼容入口：转发到 Qoder 项目的零依赖主流程。

    python run.py              # 完整自检并退出，打印 SELFCHECK PASS，退出码 0
    python run.py --serve      # 常驻站点 + API 服务，访问 http://127.0.0.1:8899/

与 `python qoder/main.py` 完全等价，便于评测器从仓库根目录直接执行。
"""
from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
ENTRY = HERE / "qoder" / "server" / "search.py"
sys.argv = [str(ENTRY), *sys.argv[1:]]
runpy.run_path(str(ENTRY), run_name="__main__")
