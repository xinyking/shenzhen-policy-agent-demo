# 深圳政务智能问答系统

查政策时，找到文件只是第一步。哪些条款与自己的情况有关、申请要准备什么、回答的依据在哪里，往往还要来回翻原文。

这个项目尝试把这些步骤放进一个对话界面：输入问题，检索深圳官方政策，结合原文给出解读，并附上可以回查的出处。你可以继续追问，也可以把需要跟进的事项整理成申请日程。

项目使用 LangGraph 编排问答流程，后端是 FastAPI，前端是 React + TypeScript，政策资料和本地记录存放在 SQLite 中。

## 可以做什么

界面提供三种使用方式：

| 模式 | 用途 |
| --- | --- |
| 数据库搜索 | 直接查看本地政策原文和出处，不需要配置模型 |
| 快速回答 | 根据检索到的资料解读问题，适合日常查询 |
| 深入研究 | 围绕问题进一步查找和补充证据，再组织回答 |

回答中可以查看引用的政策来源，继续追问条件、材料和办理流程。项目也支持上传截图辅助对照材料，以及确认后保存申请日程。

仓库附带 **5 份官方网页快照**，可以先用它们体验检索和引用。样例覆盖范围有限，也不会自动更新；涉及实际办理时，请核对官方最新文件和申请期限。

## 本地运行

准备 Python 3.12、Node.js 22.12+ 和 Git。下面以 Windows PowerShell 为例。

### 首次安装

```powershell
git clone https://github.com/xinyking/shenzhen-policy-agent-demo.git
cd shenzhen-policy-agent-demo

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps

# 用仓库自带的网页快照建立本地政策库，无需联网采集
.\.venv\Scripts\python.exe scripts/bootstrap_demo.py

cd frontend
npm ci
npm run build
cd ..
```

### 启动使用

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m app.api.server --port 8000
```

打开 [问答界面](http://127.0.0.1:8000)，就可以使用数据库搜索。需要 AI 解读时，再按下方说明配置模型。[接口文档](http://127.0.0.1:8000/docs)也由同一个服务提供。

**以后启动只需执行这一条命令。** 后端会同时提供构建好的前端页面，不必另外启动前端服务。停止时在终端按 `Ctrl+C`；8000 端口被占用时，可以通过 `--port` 换一个端口，并打开对应地址。

macOS / Linux 用户将上述 `.\.venv\Scripts\python.exe` 换成 `./.venv/bin/python`，其余安装步骤相同。

## 配置模型

在界面的模型设置中填写兼容 Chat Completions 的 API 地址、模型名和 API Key。配置保存在本机 `data/private/`，后续启动可以继续使用。

也可以通过环境变量配置，字段见 [.env.example](.env.example)。程序不会自动读取 `.env`，需要在启动服务前显式设置环境变量。

如果希望在本地没有检索结果时补查官方来源，可以额外设置 `TAVILY_API_KEY`。配置模型和搜索服务、允许联网且本地检索零命中时，系统会尝试搜索深圳政府网站并读取正文。Tavily 是可选项，使用本地资料不需要它。

## 问答是怎么完成的

一次问答大致经过以下步骤：

1. 识别问题，确定检索和研究方式。
2. 从本地政策库查找相关段落；满足条件时补查官方网页。
3. 根据证据组织回答，检查回答结构、引用和证据支持。
4. 将回答和出处返回界面，供用户继续追问或查看原文。

LangGraph 负责串起这些步骤，通过共享状态和条件分支控制后续动作。研究阶段可以调用检索、正文阅读等工具，工具调用有参数校验、权限和次数限制。界面会展示执行阶段，最终回答生成后整体显示。

本地检索使用关键词、BM25 和 RRF 融合排序，目前没有接入 Embedding 向量检索。更详细的调用关系见 [架构说明](docs/architecture.md)。

## 开发与测试

修改 Python 代码后，需要重启后端。修改前端后，执行 `npm run build` 并刷新页面；开发前端时，也可以在后端运行的同时，在 `frontend/` 下执行 `npm run dev`。

在项目根目录运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

这些测试用于检查工具、检索、接口和流程行为。真实模型的回答质量还需要单独评估，重点检查是否找全相关政策、结论是否有原文支持、回答是否遗漏关键信息。

主要代码位置：

| 目录 | 内容 |
| --- | --- |
| `app/agent/` | LangGraph 编排、上下文与模型调用 |
| `app/api/` | HTTP 接口、异步任务与执行记录 |
| `app/tools/` | 政策检索、正文阅读、截图等工具 |
| `app/knowledge/` | 网页解析、政策快照与检索索引 |
| `app/memory/`、`app/skills/` | 本地记忆与任务指引 |
| `app/evaluation/` | 检索评估 |
| `frontend/` | 对话、引用、模型设置与日程界面 |
| `data/demo/`、`data/sources/` | 演示快照与政策来源目录 |
| `scripts/`、`tests/` | 初始化、资料更新脚本与测试 |

## 数据与使用范围

这是一个在本机运行的单进程 Demo。公开政策库保存在 `data/store/`，模型密钥、个人信息和会话等本地数据保存在 `data/private/`；这些运行数据不随仓库分发，请勿提交到 Git。

当前没有公网用户认证、租户隔离或可恢复的 LangGraph Checkpoint。服务重启后，进行中的任务不能断点续跑。若要部署成多人使用的服务，还需要补充这些能力。
