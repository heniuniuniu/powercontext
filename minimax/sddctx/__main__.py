"""允许 ``python -m sddctx`` 方式调用。"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
