# 🎨 Agnes AI 生图/生视频插件

多提供商 AI 生图/生视频插件，支持 **Agnes AI / OpenAI DALL-E / SiliconFlow** 等多种后端。

> **v2.1 重大更新**：直连远端 API，无需本地 Proxy！

---

## ✨ 特性

- 🔌 **多提供商架构** — 支持 Agnes AI、OpenAI DALL-E 3、SiliconFlow 等
- 🖼️ **文生图** — 输入描述即可生成图片
- 🎨 **图改图** — 基于已有图片进行 AI 修改
- 🎬 **文生视频** — 输入描述生成视频（Agnes）
- 🎥 **图生视频** — 基于图片生成视频（Agnes）
- 🔄 **自动切换模型** — 动态切换生图/生视频模型
- 🚀 **直连 API** — 无需本地 Proxy，即装即用

---

## 📦 安装

在 AstrBot 面板 → 插件市场 → 手动安装，填入仓库地址：

```
https://github.com/tkochj/agnes-bot
```

---

## 🔧 配置

### 插件配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `provider` | AI 提供商（agnes / openai / siliconflow） | `agnes` |
| `api_key` | API Key（也可自动发现） | `""` |
| `image_model` | 默认生图模型 | `agnes-image-2.1-flash` |
| `video_model` | 默认生视频模型 | `agnes-video-v2.0` |
| `image_size` | 生图尺寸 | `1024x1024` |
| `custom_models` | 自定义模型（逗号分隔） | `""` |

### API Key 自动发现

插件会自动按以下顺序查找 Key：

1. 插件配置中手动填入的 `api_key`
2. AstrBot `cmd_config.json` 中指向 `127.0.0.1:1241` 的 Key
3. 环境变量 `AGNES_API_KEY` / `OPENAI_API_KEY` / `SILICONFLOW_API_KEY`

---

## 💬 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/画图 <描述>` | 文生图 | `/画图 一只穿着西装的猫` |
| `/改图 <描述>` | 图改图 | `/改图 换成水墨画风格` |
| `/生视频 <描述>` | 文生视频 | `/生视频 龙在云中飞舞` |
| `/图生视频 <描述>` | 图生视频 | `/图生视频 画面缓缓推近` |
| `/生图模型` | 查看可用模型 | `/生图模型` |
| `/设置模型 <名称>` | 切换模型 | `/设置模型 dall-e-3` |
| `/提供商` | 查看/切换提供商 | `/提供商 openai` |
| `/agnes` | 查看插件状态 | `/agnes` |

---

## 🔄 提供商切换

```bash
/提供商              # 查看当前提供商和可选列表
/提供商 openai       # 切换到 OpenAI DALL-E
/提供商 agnes        # 切换回 Agnes AI
/提供商 siliconflow  # 切换到 SiliconFlow
```

### 各提供商能力对比

| 功能 | Agnes | OpenAI | SiliconFlow |
|------|-------|--------|-------------|
| ✅ 文生图 | ✅ | ✅ | ✅ |
| ✅ 图改图 | ✅ | ✅ (DALL-E 2) | ❌ |
| ✅ 文生视频 | ✅ | ❌ | ❌ |
| ✅ 图生视频 | ✅ | ❌ | ❌ |

---

## 🏗️ 架构

```
旧版 (v2.0):
  插件 (main.py) ──→ 本地 Proxy (:1241) ──→ Agnes 远端 API

新版 (v2.1):
  插件 (main.py) ──→ 提供商抽象层 ──→ Agnes / OpenAI / SiliconFlow 远端 API
                      ├── AgnesProvider (直连)
                      ├── OpenAIProvider (DALL-E)
                      └── SiliconFlowProvider (开源模型)
```

---

## 📄 文件结构

```
📁 astrbot_plugin_agnes/
├── main.py              # 插件主逻辑
├── api_providers.py     # 多提供商抽象层
├── agnes_proxy.py       # （可选）旧版 Proxy，兼容使用
├── metadata.yaml        # 插件配置
├── README.md            # 本文档
└── _conf_schema.json    # 配置 Schema
```

---

## 📝 许可证

MIT License
