# PowerContext 真实运行证据

本文件记录 2026-11-13 在本机（Windows 11 / Python 3.13.9）接入**官方 PowerContext 1.5.1**
（SQLite 本地后端）后的实际输出，不是模拟数据。

## 环境与启动

```bash
python -m venv .venv
.venv/Scripts/pip install "powercontext[server]"
powercontext config init --template --output .env.powercontext
powercontext server run --env-file .env.powercontext --port 8765
```

服务就绪：`GET /health/ready` 返回 200；`GET /openapi.json` 的版本为 `1.5.1`。

## 项目写入的 Scope

```json
{
  "scope_id": "scp_1b80trae6qkt9bfdnq05sx1mj0",
  "title": "资料整理助手",
  "summary": "学习科研资料检索与跨会话记忆"
}
```

## 自检输出（真实调用 PowerContext）

```text
$ POWERCONTEXT_URL=http://127.0.0.1:8765 .venv/Scripts/python.exe main.py
SELFCHECK PASS: health / init / hybrid search / memory reuse / weekly report
{"backend": "local", "powercontext": "powercontext", "memory_backend": "powercontext",
 "hits": 5, "recent": ["混合检索", "向量索引刷新"], "topTag": "检索"}
exit=0
```

## 日志中的真实读写记录

`qoder/logs/run.log`（节选，完整文件由每次运行追加）：

```text
[2026-11-13T08:11:47] powercontext.read {"backend": "powercontext", "scope": "scp_1b80trae6qkt9bfdnq05sx1mj0", "user": "selfcheck", "state": {"recent": ["混合检索", "向量索引刷新"], "topTag": "数据库"}, "entries": 6}
[2026-11-13T08:11:47] powercontext.recall {"backend": "powercontext", "scope": "scp_1b80trae6qkt9bfdnq05sx1mj0", "query": "向量索引刷新", "tags": ["数据库"]}
[2026-11-13T08:11:47] powercontext.write {"backend": "powercontext", "scope": "scp_1b80trae6qkt9bfdnq05sx1mj0", "user": "selfcheck", "text": "主题=向量索引刷新；标签=数据库；用户=selfcheck；时间=2026-11-13T08:11:47", "artifact": {"family": "atomic-memory", "artifact_id": "art_2fb585d87be7443ebb94b316b33f68b0", "revision": 1}}
[2026-11-13T08:11:47] powercontext.recall {"backend": "powercontext", "scope": "scp_1b80trae6qkt9bfdnq05sx1mj0", "query": "混合检索", "tags": ["数据库"]}
```

## 直接查询 PowerContext 的 Atomic Memory

```bash
curl -X POST http://127.0.0.1:8765/v1/memory/entries/list \
  -H 'Content-Type: application/json' \
  -d '{"scope_id":"scp_1b80trae6qkt9bfdnq05sx1mj0","limit":10}'
```

返回的条目（节选）：

```text
检索记录 | 主题=向量索引刷新；标签=数据库；用户=selfcheck；时间=2026-11-13T08:11:47
检索记录 | 主题=混合检索；标签=检索；用户=demo；时间=2026-11-13T08:12:02
```

搜索接口同样可召回这些记忆：

```bash
curl -X POST http://127.0.0.1:8765/v1/memory/search \
  -H 'Content-Type: application/json' \
  -d '{"scope_id":"scp_1b80trae6qkt9bfdnq05sx1mj0","query":"混合检索","limit":5}'
# mode=text, matched_by=["text"], 命中上面的检索记录
```

## 项目 HTTP 接口的响应

```text
$ curl -s http://127.0.0.1:8899/health
{"ok": true, "backend": "local", "powercontext": "powercontext", "version": "1.1", "chunks": 5}

$ curl -s -X POST http://127.0.0.1:8899/search -H 'Content-Type: application/json' \
       -d '{"query":"混合检索","user":"demo"}'
{"backend": "local", "memory_backend": "powercontext",
 "recalled_tags": ["检索"],
 "memo": {"recent": ["混合检索", "向量索引刷新"], "topTag": "检索"}, ...}
```

`memory_backend=powercontext` 表示本轮记忆读写确实走了 PowerContext；
`recalled_tags` 是上一轮记忆参与本轮排序的证据；服务不可达时才会降级为 `local`。
