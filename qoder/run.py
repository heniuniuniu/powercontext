"""qoder/ 目录内的零安装入口，与 `python main.py` 完全等价。

    python run.py              # 完整自检并退出，打印 SELFCHECK PASS，退出码 0
    python run.py --serve      # 常驻站点 + API，访问 http://127.0.0.1:8899/

仓库根目录的 run.py 会转发到同一套主流程代码。
"""
from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
ENTRY = HERE / "server" / "search.py"
sys.argv = [str(ENTRY), *sys.argv[1:]]
runpy.run_path(str(ENTRY), run_name="__main__")
