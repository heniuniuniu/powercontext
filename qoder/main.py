"""项目统一入口：零第三方依赖，一条命令启动或自检。

    python main.py --selfcheck   # 端到端自检，打印 SELFCHECK PASS
    python main.py               # 启动站点 + API，http://127.0.0.1:8899/

真实 seekdb / PowerContext 接入是可选的；未配置时自动使用内置语料与
本地记忆文件，主流程完整可运行。
"""
from pathlib import Path
import runpy
import sys

ENTRY = Path(__file__).resolve().parent / "server" / "search.py"
sys.argv = [str(ENTRY), *sys.argv[1:]]
runpy.run_path(str(ENTRY), run_name="__main__")
