# 深圳政务智能问答系统

基于 LangGraph 的政策问答 Demo，支持政策检索与解读、官方来源补查、引用展示及申请日程管理。回答围绕适用人群、申请条件、材料和办理流程组织，用户可以继续追问。

技术栈：Python、FastAPI、LangGraph、SQLite、HTTPX、Tavily、React、TypeScript、Vite。

## 核心设计

- 政策分类、研究和日程规划分工，通过共享状态、条件路由和有界补查循环协作。
- 将检索、正文阅读和日程操作拆为工具，校验参数与调用权限，限制重复动作、调用次数和执行时间。
- 为研究、生成和核查阶段分别组织上下文，加载任务 Skill，结合本地会话记忆保留追问背景。
- 关键词、BM25 与 RRF 融合检索，结合政策年度排序；校验回答结构、引用 ID 和证据支持情况。
- 异步执行并推送阶段状态，最终回答整体展示。阶段记录展示任务进度，不展示模型内部思维链。

## 本地运行

需要 Python 3.12 和 Node.js 22.12+。在仓库目录执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt
python -m pip install -e . --no-deps
python scripts/bootstrap_demo.py
cd frontend
npm ci
npm run build
cd ..
policy-api --port 8000
```

macOS/Linux 将激活命令替换为 `source .venv/bin/activate`。

打开 http://127.0.0.1:8000，接口文档位于 http://127.0.0.1:8000/docs。端口被占用时可指定其他端口。

初始化会从随仓库提供的 5 份官方网页快照建立 SQLite 数据库，无需联网或模型。快照用于演示检索和引用，不表示政策当前仍可申请。未配置模型时可以使用数据库搜索，AI 解读需要另行配置模型。

## 配置模型和搜索

在界面的模型设置中填写兼容 Chat Completions 的 API 地址、模型名和密钥，配置保存在本机 `data/private/`。也可显式设置 `.env.example` 所列环境变量，程序不会自动加载 `.env`。

Tavily 为可选项：设置 `TAVILY_API_KEY` 后，在允许联网且本地检索零命中时补查深圳政府官方来源，并读取正文作为证据。联网不保证找到结果，模型与搜索服务的费用以服务商为准。

## 目录

| 路径 | 用途 |
| --- | --- |
| `app/agent/` | 图编排、共享状态、上下文、模型调用与子 Agent |
| `app/api/` | HTTP 接口、异步任务和执行记录 |
| `app/tools/` | 工具注册、政策与截图工具、联网补查 |
| `app/knowledge/` | 网页解析、快照、SQLite 与融合检索 |
| `app/memory/` | 本地个人信息与会话记忆 |
| `app/skills/` | Skill 选择、加载与任务说明 |
| `app/evaluation/` | 检索评估 |
| `frontend/` | 对话、证据、配置与日程界面 |
| `data/demo/` | 官方网页样例与来源清单 |
| `data/sources/` | 可用于扩充资料库的官方来源目录 |
| `scripts/` | 离线初始化和来源更新脚本 |
| `tests/` | 工具、检索、接口与流程测试 |

架构详见 [docs/architecture.md](docs/architecture.md)。

## 验证

安装依赖并完成前端构建后运行：

```sh
python -m unittest discover -s tests
```

离线测试不能代替真实模型的回答质量评估。模型效果应使用独立标注问题，分别检查检索召回、事实支持和回答完整性。

## 使用范围

这是本地单进程 Demo，未提供面向公网的用户认证与租户隔离。API 密钥、会话、个人信息和运行数据库均不随仓库提供，也不应提交到 Git。当前记忆使用 SQLite，未接入 Redis、向量数据库或可恢复的 LangGraph Checkpoint。政策原文与适用期限以官方最新发布为准。
