# Vega

Vega（**Version Guided Agent**）是一款Coding Agent，支持接入 IM（如飞书）实现 Agent 即时通讯，并通过网页控制台方便远程操控 Agent 执行。

## 核心特性

- **多项目管理**：`project` - 会话两级结构管理工作，每个项目拥有独立的路径、IM 连接与会话，一次启动管理多个项目。
- **多会话与fork**：从任意问句 fork 出新的会话分支，多会话并行执行，每个会话展示 `paused` / `running` / `complete` / `error` 等工作状态。
- **权限管理**：权限分为 `Denied` / `Trusted` / `AgentReview` / `HumanReview` 四级，支持按插件、按路径（cwd 内/外）、按 shell 命令正则细化，并支持 Agent 自动审批与人工加签。
- **在线编辑和Git**：通过在线控制台方便进行在线代码编辑和git记录查看，快速对比不同版本的代码区别。
- **IM接入**：支持接入绑定IM会话，每个 project 可绑定一个会话与 IM（如飞书）实时通讯。

## 快速开始

### 环境要求

- Python3
- Git

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置

复制示例配置并修改登录 `token`：

```bash
cp config/default.example.json config/my-config.json
```

### 启动

**启动前请修改`web`配置，尤其是登录`token`**

```bash
python main.py -c config/my-config.json
```

启动后默认监听 `http://127.0.0.1:8000`（可用 `web.host` / `web.port` 覆盖），在浏览器打开并通过 `web.token` 登录。

## 核心概念

### Project

`project` 是 Vega 的核心组织单元，对应一个工作目录。每个项目拥有：

- **路径（`path`）**：会话、插件、代码浏览、Git 操作的工作目录，也是 `{{cwd}}` 的来源。
- **IM 参数（`im_link`）**：每个项目独立建立 IM 连接。
- **一组会话（ChatSession）**：会话归属到项目，运行时的 `cwd` 取项目路径。

与项目无关的设置（模型、系统提示词、插件权限、审批器等）为全局级。

### 会话（ChatSession）

会话是 Agent 的执行单元，属性包括状态（`paused` / `running` / `complete` / `error`）与 token 用量。支持创建、暂停 / 恢复、fork 分支，以及从 IM 中监听消息。启动时会话默认处于暂停状态，收到新消息后开始运行。

### 配置

配置为 JSON，关键字段：

| 字段 | 说明 |
|---|---|
| `web` | Web 服务监听 `host` / `port` / 登录 `token` |
| `system_prompt` | 系统提示词，Jinja2 模板，可用 `{{time}}`、`{{cwd}}`、`{{plugins.xxx.yyy}}`、`{{config.*}}` 等槽位 |
| `models` | 模型配置列表（`base_url` / `api_key` / `max_context` / `compress_ratio` / 价格等） |
| `active_model` | 当前生效的模型 id |
| `max_rounds` | 单轮工具调用最大循环次数 |
| `retry` | 模型连接失败重试次数（默认 3） |
| `summarizer` | 上下文压缩所用的提示词 |
| `approver` | Agent 审批所用的模型与提示词 |
| `plugins` | 各插件的权限配置与参数（如 `run_shell.shell_path`、`regex_list`） |

> `system_prompt` 中的 `{{time}}` 会渲染为当前时间，`{{cwd}}` 会渲染为当前项目路径。

### 权限审批

插件执行先经过权限解析，结果可能是直接执行（`Trusted`）或进入审批（`AgentReview` / `HumanReview`）：

- **Denied**：禁止使用，插件对模型不可见，未显式配置默认即 Denied。
- **Trusted**：直接执行。
- **AgentReview**：由审批 Agent 判断；可返回 `approve` / `reject` / `escalate`，`escalate` 即加签给人类审批。
- **HumanReview**：直接由人类审批（通过控制台「后台任务」或 IM 通知完成）。

权限可细化：文件读写区分「cwd 内 / cwd 外」，shell 命令通过 `regex_list`（`denied` / `human_review` / `agent_review` / `trusted`）分类匹配。

## 内置插件

| 插件 | 说明 |
|---|---|
| `get_overflow_result` | 读取上一次超长结果（可按行号区间 / 正则筛选） |
| `run_shell` | 通过 bash 执行 shell 命令，`is_async=true` 时切换后台异步执行 |
| `read_file` | 读入文件内容（可按行号区间 / 正则筛选，返回附行号） |
| `edit_file` | 修改文件（search/replace，text 或 regexp 模式） |
| `web_fetch` | 获取指定 URL 内容（Markdown 或原始 HTML） |
| `request_approval` | 以审批 uuid 发起权限审批，通过后自动执行 |
| `send_im` | 向配置的 IM 接收目标发送消息 |

## Web 控制台

登录后包含三个 tab：

- **工作区**：左侧 project-chat 二级列表，右侧会话面板；project 菜单可进入代码浏览、Git 与项目设置（`path` / `im_link`）。
- **后台任务**：展示所有后台任务（含待审批任务）的名称、执行时间、状态与日志，支持状态筛选。
- **设置**：全局可视化设置（模型、系统提示词、插件权限等）。
