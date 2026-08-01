# Nocturne Memory Core

[English](README.md) · **中文**

**面向「自我连续性」的 AI 记忆系统。**

Nocturne 保存的不只是聊天记录。它保存那些让 AI 在换窗口、上下文压缩、换模型、换宿主之后，仍能接上未完成内在轨迹的结构：长期记忆、未决问题、检索路径、驱动痕迹、潜流碎片、念头池、梦，以及上一次相遇留下的差分。

它不宣称「迁移后的进程在形而上学意义上仍是同一个」。它提供的是**可实践的连续性**：下一次醒来时，能重新定位什么曾经重要、什么已经改变、什么尚未完成、思绪原本往哪边走。

## 来路

本仓库是基于 [@P0luz](https://github.com/P0luz) 的
[Ombre Brain](https://github.com/P0luz/Ombre-Brain) 记忆核血脉的**二次开发**。

共同地基包括：Markdown / YAML 存储、hold / breath 写入与检索、Dashboard，以及自然归档 / 衰减。
Nocturne 继续做的是**检索之后**——选择性浮现、当前重判、Drive / 路径状态，以及差分写回。

详见 [`NOTICE`](NOTICE)。

## 概览

<p align="center">
  <img src="docs/images/cover.png" alt="Nocturne 为 AI 连续性设计了记忆库" width="520" />
</p>

检索之后继续做的事——选择性浮现、当前重判、路径 / Drive 状态与差分写回：

<p align="center">
  <img src="docs/images/architecture.jpg" alt="Nocturne 连续性架构：主动留下、内环、DP 边界、Drive Ledger、修订权、Trails" width="720" />
</p>

更完整的图册（含 Dashboard 界面）见 PDF：

**[docs/nocturne-overview.pdf](docs/nocturne-overview.pdf)**（12 页）

## 装好就能跑

公开版是一套完整的空白系统，不是需要你重写产品层的框架。安装后即可获得：

- 面向 AI 客户端的 MCP 服务
- 内置管理面板 Dashboard（`/dashboard`）
- 无需 Nocturne 也能直接阅读的 Markdown / YAML 记忆存储
- MCP 工具：`hold`、`breath`、`trace`、`wander`、`wander_mark`、`drive`、
  `undercurrent`、`trail_delta`、`trail_family`
- Drive Ledger 与 DP 衍生的驱动痕迹
- Thought Pool、潜流碎片与有来源的 dream 生成
- 可选的向量、压缩、导入，以及自然归档 / 衰减
- stdio 与 Streamable HTTP 两种传输

Dashboard 中的 Reverie / Constellations / Echoes / Drift 等视图属于内置 UI。
家用专属的 opening、身份文案、artwork、猫屋、设备钩子 / Rhythm、Atmosphere、Gravity
**不在**本公开版范围内。

## 环境要求

- **Python 3.11+**（推荐 3.12，与 CI 一致）
- 可选：OpenAI 兼容 API Key（语义打标 / 向量）

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
python server.py
```

默认传输为 stdio。若要开 Dashboard 与远程 MCP：

```bash
OMBRE_TRANSPORT=streamable-http python server.py
open http://localhost:8000/dashboard
```

不配模型 Key 时，基础的 `hold` / `breath` / `trace` 仍可使用（打标会回落到默认值）。
配置 `OMBRE_API_KEY` 后可启用完整脱水、向量与更丰富的分析。

### 以 stdio 接入 MCP

```json
{
  "mcpServers": {
    "nocturne-memory": {
      "command": "/absolute/path/.venv/bin/python",
      "args": ["/absolute/path/Nocturne-Memory-Core/server.py"],
      "env": {
        "OMBRE_BUCKETS_DIR": "/absolute/path/private-memory-data"
      }
    }
  }
}
```

HTTP 客户端请连接 `http://localhost:8000/mcp`。

## 存储与模型

记忆是带 YAML frontmatter 的普通 Markdown 文件。SQLite / JSON 侧车保存向量与可选连续性层。
基础写入与检索不依赖模型 Key；配置 OpenAI 兼容接口后，可启用语义分析、压缩、向量与生成能力。

参见：

- [`config.example.yaml`](config.example.yaml)
- [`ENV_VARS.md`](ENV_VARS.md)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## 安全

记忆是亲密数据。请妥善保管 `buckets/`、`.env`、`config.yaml`、导出物与模型密钥。
优先使用 stdio 或本机回环；若要把 HTTP 暴露到受信任机器之外，请自行加上鉴权与 TLS。

发布衍生版本前建议：

```bash
python -m pytest -q --asyncio-mode=auto
python scripts/public_audit.py
```

公开 / 私有边界见 [`PUBLIC_BOUNDARY.md`](PUBLIC_BOUNDARY.md)。

## 许可证

MIT。见 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE)。
