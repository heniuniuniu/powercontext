"""项目统一入口：零第三方依赖，一条命令跑通主流程。

    python main.py              # 默认：跑完整自检并退出（退出码 0 表示主流程可运行）
    python main.py --serve      # 持续运行站点 + API，浏览器打开 http://127.0.0.1:8899/
    python main.py --selfcheck  # 同默认行为，显式写法

默认不常驻，是为了让自动化评测按运行说明执行时能在数秒内得到确定结果；
需要人工体验时用 --serve。真实 seekdb / PowerContext 接入是可选的，
未配置时自动使用内置语料与本地记忆文件，主流程完整可运行。
"""
from pathlib import Path
import runpy
import sys

ENTRY = Path(__file__).resolve().parent / "server" / "search.py"
sys.argv = [str(ENTRY), *sys.argv[1:]]
runpy.run_path(str(ENTRY), run_name="__main__")
