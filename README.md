# 🎨 Agnes AI 生图/生视频插件

> 在 AstrBot 中一键调用 Agnes AI 生图与生视频能力，支持 agnes-image-2.1-flash 和 agnes-video-v2.0。

---

## 📦 安装

### 方式一：面板安装
在 AstrBot 面板 → 插件市场 → 搜索 `agnes` → 安装。

### 方式二：手动安装
1. 将本目录放入 `AstrBot/data/plugins/`
2. 在面板重载插件

---

## 🔧 配置

### 方式一：面板配置
AstrBot 面板 → 插件管理 → astrbot_plugin_agnes → 配置 → 填入 `api_key`

### 方式二：使用已有 OpenAI provider Key
如果已在 `cmd_config.json` 的 `openai` provider 中配置了指向 `127.0.0.1:1241` 的 key，插件会自动读取，无需再次配置。

---

## 💬 命令

### 🖼️ 文生图 / 文生视频

| 命令 | 说明 | 示例 |
|---|---|---|
| `/画图 <描述>` | 根据描述生成图片 | `/画图 波奇酱孤独摇滚风格弹吉他` |
| `/生视频 <描述>` | 根据描述生成视频 | `/生视频 一只龙在云中飞翔` |
| `/生图模型` | 查看当前使用的生图模型 | `/生图模型` |

### 🎨 图改图（基于已有图片修改）

| 命令 | 说明 | 用法 |
|---|---|---|
| `/改图 <描述>` | 基于上传的图片进行AI改图 | ① 先发一张图片 ② 再发 `/改图 把这只猫换成蜡笔涂鸦风格` |

**图改图示例：**
```
用户： [发送一张猫咪照片]
用户： /改图 把这只猫画成二次元Q版风格，粉色背景，加星星和爱心

Agnes： 🎨 正在改图「把这只猫画成二次元Q版...」...请稍等
       🔄 改图完成！ [返回改好的图片]
```

### 🎬 图生视频（基于已有图片生成视频）

| 命令 | 说明 | 用法 |
|---|---|---|
| `/图生视频 <描述>` | 基于上传的图片生成动态视频 | ① 先发一张图片 ② 再发 `/图生视频 画面缓缓推近` |

**图生视频示例：**
```
用户： [发送一张风景照片]
用户： /图生视频 云朵缓慢飘动，阳光洒在草地上

Agnes： 🎬 正在基于图片生成视频...可能需要 1-2 分钟
       🎬 图生视频完成！ [返回生成的视频]
```

---

## 🖥️ 前提：启动 Agnes Proxy

插件依赖本地 Agnes Proxy 服务，需要先启动代理：

```bash
# 安装
pip install agnes-proxy

# 启动（默认端口 1241）
python -m agnes_proxy
```

确保 Proxy 启动后，再使用插件命令。

---

## 📂 文件说明

| 文件 | 说明 |
|---|---|
| `main.py` | 插件主逻辑，处理命令分发和调用 |
| `agnes_proxy.py` | Agnes API → OpenAI 兼容格式的 HTTP Proxy（端口 1241） |
| `agnes_tool.py` | CLI 工具，支持文生图、图改图、文生视频、图生视频 |
| `metadata.yaml` | 插件元信息配置 |

## 🛠️ agnes_tool.py CLI 用法（给 AI Agent 调用）

```bash
# 文生图
python agnes_tool.py image "一只穿着西装的猫猫" 

# 图改图
python agnes_tool.py img2img "改成蜡笔涂鸦风格" https://example.com/image.jpg

# 文生视频
python agnes_tool.py video "一条龙在云中飞翔"

# 图生视频
python agnes_tool.py img2video "画面缓缓推近" https://example.com/image.jpg
```

输出格式：
- 图片：`RESULT_IMAGE_URL=https://...`
- 视频：`RESULT_VIDEO_URL=https://...`

## 🌐 API 接口文档（OpenAI 兼容）

所有接口通过本地 Proxy（`http://127.0.0.1:1241`）暴露：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/models` | GET | 列出可用模型 |
| `/v1/chat/completions` | POST | 文本对话（兼容 OpenAI） |
| `/v1/images/generations` | POST | 文生图 / 图改图 |
| `/v1/videos` | POST | 文生视频 / 图生视频（异步） |
| `/v1/videos/<task_id>` | GET | 查询视频生成状态 |
| `/health` | GET | 健康检查 |

### 图改图 API 示例

```python
import requests

resp = requests.post("http://127.0.0.1:1241/v1/images/generations", json={
    "model": "agnes-image-2.1-flash",
    "prompt": "改成蜡笔涂鸦风格，粉色蓝色为主，加星星爱心",
    "image": "https://example.com/input.jpg",  # ← 传入原图
    "n": 1,
})
print(resp.json()["data"][0]["url"])
```

### 图生视频 API 示例

```python
import requests, time

# 1. 创建视频任务
resp = requests.post("http://127.0.0.1:1241/v1/videos", json={
    "model": "agnes-video-v2.0",
    "prompt": "云朵缓慢飘动，阳光洒在草地上",
    "image": "https://example.com/input.jpg",
})
task_id = resp.json()["id"]

# 2. 轮询等待完成
for i in range(60):
    time.sleep(5)
    status = requests.get(f"http://127.0.0.1:1241/v1/videos/{task_id}").json()
    if status["status"] == "completed":
        print(status["video"])  # ← 视频 URL
        break
```

---

## 🛠️ 开发

- 插件默认模型：`agnes-image-2.1-flash`（生图）、`agnes-video-v2.0`（生视频）
- 如需切换模型，可在 `main.py` 中修改 `DEFAULT_IMAGE_MODEL` 和 `DEFAULT_VIDEO_MODEL`

---

## 📄 License

MIT
