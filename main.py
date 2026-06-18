"""
🎨 Agnes AI 生图/生视频插件 v2.1 - 多提供商架构
直连远端 API，无需本地 Proxy，支持多模型后端

支持的提供商：
  - agnes:      Agnes AI（生图/生视频，默认）
  - openai:     OpenAI DALL-E 3（生图）
  - siliconflow: SiliconFlow 开源模型（生图）

命令：
  /画图 <描述>        文生图
  /改图 <描述>        图改图（需先发图片）
  /生视频 <描述>      文生视频
  /图生视频 <描述>    图生视频（需先发图片）
  /生图模型           查看可用模型
  /设置模型 <模型名>  切换模型
  /提供商             查看/切换提供商
"""
import os
import sys
import json
import time
import re
import glob

import requests
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image, Video

from .api_providers import create_provider, get_all_provider_names, AgnesProvider

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(PLUGIN_DIR, ".last_image_cache.txt")


def _get_prompt(event: AstrMessageEvent) -> str:
    """从消息中提取命令参数（兼容各平台）"""
    try:
        args = event.get_args()
        if args:
            return " ".join(args)
    except AttributeError:
        pass
    raw = getattr(event, 'message_str', '') or ''
    return re.sub(r"^/\S*\s*", "", raw, count=1).strip()


def _find_api_key(configured_key: str = "") -> str:
    """
    自动发现 API Key，优先级：
    1. 插件配置中手动填入的 api_key
    2. AstrBot provider 配置中指向 127.0.0.1:1241 的 key（向后兼容）
    3. 环境变量 AGNES_API_KEY / OPENAI_API_KEY
    """
    if configured_key:
        logger.info("[Agnes] 使用插件配置的 api_key")
        return configured_key

    # 搜索 cmd_config.json
    try:
        search_roots = [
            os.path.abspath(os.path.join(PLUGIN_DIR, "..", "..", "..", "..")),
            os.path.abspath(os.path.join(PLUGIN_DIR, "..", "..", "..")),
        ]
        for root in search_roots:
            patterns = ["cmd_config.json", "*_layout*.json", "*_config*.json"]
            for pattern in patterns:
                for fp in glob.glob(os.path.join(root, "**", pattern), recursive=True):
                    try:
                        with open(fp, "r", encoding="utf-8-sig") as f:
                            cfg = json.load(f)
                    except Exception:
                        continue

                    def _find(obj, depth=0):
                        if depth > 8:
                            return None
                        if isinstance(obj, dict):
                            # 找指向 127.0.0.1:1241 的 key（旧版 Proxy 兼容）
                            api_base = obj.get("api_base", "")
                            if "127.0.0.1" in api_base and "1241" in api_base:
                                key_list = obj.get("key", [])
                                if isinstance(key_list, list) and key_list and key_list[0]:
                                    return key_list[0].strip()
                            for v in obj.values():
                                r = _find(v, depth + 1)
                                if r:
                                    return r
                        elif isinstance(obj, list):
                            for item in obj:
                                r = _find(item, depth + 1)
                                if r:
                                    return r
                        return None

                    found = _find(cfg)
                    if found:
                        logger.info("[Agnes] 从配置自动发现 API Key ✓")
                        return found
    except Exception as e:
        logger.debug(f"[Agnes] 搜索配置 Key 时出错: {e}")

    # 环境变量
    for env_name in ["AGNES_API_KEY", "OPENAI_API_KEY", "SILICONFLOW_API_KEY"]:
        val = os.environ.get(env_name, "")
        if val:
            logger.info(f"[Agnes] 使用环境变量 {env_name}")
            return val

    logger.warning("[Agnes] 未找到 API Key")
    return ""


def _upload_image(file_path: str) -> str:
    """将本地图片上传为公网可访问 URL"""
    if not file_path or not os.path.exists(file_path):
        return file_path
    if file_path.startswith(("http://", "https://", "data:")):
        return file_path

    try:
        logger.info(f"[Agnes] 上传本地图片: {file_path}")
        with open(file_path, "rb") as f:
            ext = os.path.splitext(file_path)[1].lower() or ".jpg"
            resp = requests.post(
                "https://freeimage.host/api/1/upload",
                data={"key": "6d207e02198a847aa98d0a2a901485a5", "format": "json"},
                files={"source": (f"image{ext}", f, "image/jpeg")},
                timeout=30,
            )
        if resp.status_code == 200:
            url = resp.json().get("image", {}).get("url", "")
            if url:
                logger.info(f"[Agnes] 上传成功: {url}")
                return url
            logger.warning(f"[Agnes] freeimage 返回异常: {resp.text[:100]}")
        else:
            logger.warning(f"[Agnes] 上传失败: {resp.status_code}")
    except Exception as e:
        logger.error(f"[Agnes] 上传异常: {e}")

    # 回退 data URI
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(file_path)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".gif": "image/gif", ".webp": "image/webp"}
        mime = mime_map.get(ext, "image/jpeg")
        b64 = __import__("base64").b64encode(data).decode()
        return f"data:{mime};base64,{b64}"
    except:
        return file_path


def _find_latest_image() -> str:
    """从 temp 目录找最新图片"""
    try:
        temp_dir = os.path.abspath(
            os.path.join(PLUGIN_DIR, "..", "..", "..", "..", "data", "temp")
        )
        if not os.path.exists(temp_dir):
            return ""

        imgs = []
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp"):
            imgs.extend(glob.glob(os.path.join(temp_dir, ext)))
        if not imgs:
            return ""
        latest = max(imgs, key=os.path.getmtime)
        logger.info(f"[Agnes] 从 temp 找到最新图片: {os.path.basename(latest)}")
        return latest
    except Exception as e:
        logger.debug(f"[Agnes] 查找 temp 图片失败: {e}")
        return ""


def _read_cache() -> str:
    """读取缓存的图片 URL"""
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def _write_cache(url: str):
    """写入缓存"""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(url)
    except Exception:
        pass


@register("astrbot_plugin_agnes", "🎨 Agnes AI 生图/生视频", "多提供商 AI 生图/生视频插件", "v2.1.0")
class AgnesPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        # 用户配置
        api_key = _find_api_key(self.config.get("api_key", ""))
        provider_name = self.config.get("provider", "agnes").lower()
        self.last_image_url = _read_cache()

        # 生图/生视频模型
        self.image_model = self.config.get("image_model", "agnes-image-2.1-flash")
        self.video_model = self.config.get("video_model", "agnes-video-v2.0")
        raw_custom = self.config.get("custom_models", "")
        self.custom_models = [m.strip() for m in raw_custom.split(",") if m.strip()]

        # 提供商配置
        self._provider_name = provider_name
        self._provider = self._init_provider(provider_name, api_key)

        if not api_key:
            logger.warning("[Agnes] ⚠️ 未配置 API Key，请检查配置")
        else:
            logger.info(f"[Agnes] ✅ 初始化完成 | 提供商: {provider_name} | 图: {self.image_model} | 视频: {self.video_model}")

    # ─── 提供商管理 ───

    def _init_provider(self, name: str, api_key: str):
        """初始化提供商，失败时回退到 Agnes"""
        try:
            # 如果是 Agnes，构造时已知道 api_key
            if name == "agnes":
                return AgnesProvider(api_key)
            return create_provider(name, api_key)
        except Exception as e:
            logger.error(f"[Agnes] 初始化提供商 {name} 失败: {e}，回退到 Agnes")
            self._provider_name = "agnes"
            return AgnesProvider(api_key)

    @property
    def provider(self):
        return self._provider

    # ─── 图片提取 ───

    def _extract_image(self, event: AstrMessageEvent, prompt: str = "") -> str:
        """
        从事件中提取图片 URL，优先级：
        消息组件 > 消息文本 URL > prompt URL > 缓存 > temp 目录最新图
        """
        img_url = None

        # 1. 消息组件
        try:
            for comp in event.get_messages():
                if hasattr(comp, "type") and comp.type == "image":
                    img_url = comp.url or comp.file
                    break
                if hasattr(comp, "url") and comp.url:
                    img_url = comp.url
                    break
                if hasattr(comp, "file") and comp.file:
                    img_url = comp.file
                    break
        except Exception:
            pass

        # 2. 文本中的 URL
        if not img_url:
            raw = getattr(event, "message_str", "") or ""
            urls = re.findall(r"https?://[^\s]+\.(?:jpg|jpeg|png|gif|webp|bmp)", raw, re.IGNORECASE)
            if urls:
                img_url = urls[0]

        # 3. prompt 中的 URL
        if not img_url and prompt:
            urls = re.findall(r"https?://[^\s]+\.(?:jpg|jpeg|png|gif|webp|bmp)", prompt, re.IGNORECASE)
            if urls:
                img_url = urls[0]

        # 4. 缓存
        if not img_url and self.last_image_url:
            img_url = self.last_image_url

        # 5. temp 目录
        if not img_url:
            img_url = _find_latest_image()

        # 6. 工作区缓存（AI 助手上传的）
        if not img_url:
            try:
                ws_root = os.path.abspath(
                    os.path.join(PLUGIN_DIR, "..", "..", "..", "..", "data", "workspaces")
                )
                if os.path.exists(ws_root):
                    for root, _, files in os.walk(ws_root):
                        if "last_image_url.txt" in files:
                            with open(os.path.join(root, "last_image_url.txt"), "r", encoding="utf-8") as f:
                                cached = f.read().strip()
                                if cached:
                                    img_url = cached
                                    logger.info(f"[Agnes] 从 workspace 缓存读取图片")
                            break
            except Exception:
                pass

        # 缓存本次结果
        if img_url:
            self.last_image_url = img_url
            _write_cache(img_url)

        return img_url

    # ─── 命令：缓存图片 ───

    @filter.command("缓存图片")
    async def cache_image(self, event: AstrMessageEvent):
        """/缓存图片 - 手动缓存当前消息中的图片"""
        url = self._extract_image(event)
        if url:
            yield event.plain_result(f"✅ 已缓存图片")
        else:
            yield event.plain_result("❌ 当前消息中没有找到图片")

    # ─── 命令：生图 ───

    @filter.command("画图")
    async def draw_image(self, event: AstrMessageEvent):
        """/画图 <描述> - AI 文生图"""
        prompt = _get_prompt(event)
        if not prompt:
            yield event.plain_result(
                "请描述你想画的内容~\n"
                "例：/画图 一只穿着西装的猫猫在办公桌前喝咖啡"
            )
            return

        yield event.plain_result(f"🎨 正在画「{prompt}」...")

        try:
            result = self.provider.text_to_image(prompt, model=self.image_model,
                                                  size=self.config.get("image_size", "1024x1024"))
            if "url" in result:
                chain = [Image(file=result["url"], url=result["url"])]
                yield event.chain_result(chain)
            elif "b64_json" in result:
                yield event.plain_result("✅ 生成完成（base64 格式）")
            else:
                yield event.plain_result("✅ 生成完成")
        except RuntimeError as e:
            yield event.plain_result(f"❌ {e}")
        except requests.exceptions.ConnectionError:
            yield event.plain_result("❌ 网络连接失败，请检查网络")
        except Exception as e:
            yield event.plain_result(f"❌ 出错: {str(e)}")

    # ─── 命令：改图 ───

    @filter.command("改图")
    async def edit_image(self, event: AstrMessageEvent):
        """/改图 <描述> - 基于图片进行 AI 改图
        用法：先发一张图片，再发 /改图 把猫换成蜡笔风格"""
        prompt = _get_prompt(event)
        if not prompt:
            yield event.plain_result(
                "请描述你想怎么改图~\n"
                "先发一张图片，然后：/改图 把这只猫换成蜡笔涂鸦风格"
            )
            return

        image_url = self._extract_image(event, prompt)
        if not image_url:
            yield event.plain_result("❌ 没找到图片！请先上传图片")
            return

        # 本地文件转公网 URL
        if not image_url.startswith(("http://", "https://", "data:")):
            yield event.plain_result("📤 正在上传图片...")
            public_url = _upload_image(image_url)
            if public_url and (public_url.startswith(("http", "data:"))):
                image_url = public_url
            else:
                yield event.plain_result("❌ 无法上传图片到公网")
                return

        yield event.plain_result(f"🎨 正在改图「{prompt}」...")

        try:
            result = self.provider.image_to_image(prompt, image_url, model=self.image_model)
            if "url" in result:
                chain = [Plain("🔄 改图完成！"), Image(file=result["url"], url=result["url"])]
                yield event.chain_result(chain)
            else:
                yield event.plain_result("✅ 改图完成")
        except RuntimeError as e:
            yield event.plain_result(f"❌ {e}")
        except Exception as e:
            yield event.plain_result(f"❌ 出错: {str(e)}")

    # ─── 命令：生视频 ───

    @filter.command("生视频")
    async def generate_video(self, event: AstrMessageEvent):
        """/生视频 <描述> - AI 文生视频"""
        prompt = _get_prompt(event)
        if not prompt:
            yield event.plain_result(
                "请描述视频内容~\n"
                "例：/生视频 一条龙在云中飞舞"
            )
            return

        yield event.plain_result(f"🎬 正在创建视频「{prompt}」...")

        try:
            task = self.provider.text_to_video(prompt, model=self.video_model)
            task_id = task.get("task_id", "")
            yield event.plain_result(f"🎬 视频任务已提交，正在生成...")

            start_ts = time.time()
            last_notify = 0
            for _ in range(60):
                time.sleep(5)
                try:
                    status = self.provider.get_video_status(task_id)
                    elapsed = int(time.time() - start_ts)
                    if elapsed - last_notify >= 30:
                        yield event.plain_result(f"⏳ 视频生成中... 已等待 {elapsed} 秒")
                        last_notify = elapsed
                    if status.get("status") == "completed" and status.get("video"):
                        chain = [Video.fromURL(status["video"])]
                        yield event.chain_result(chain)
                        return
                    if status.get("status") in ("failed", "timeout"):
                        yield event.plain_result(f"❌ 视频生成失败: {status.get('error', '未知错误')}")
                        return
                except Exception:
                    pass
            yield event.plain_result("⏰ 视频生成超时，请稍后重试")
        except RuntimeError as e:
            yield event.plain_result(f"❌ {e}")
        except Exception as e:
            yield event.plain_result(f"❌ 出错: {str(e)}")

    # ─── 命令：图生视频 ───

    @filter.command("图生视频")
    async def image_to_video(self, event: AstrMessageEvent):
        """/图生视频 <描述> - 基于图片生成视频"""
        prompt = _get_prompt(event)
        if not prompt:
            yield event.plain_result(
                "请描述视频效果~\n"
                "先发一张图片，然后：/图生视频 画面缓缓推近"
            )
            return

        image_url = self._extract_image(event, prompt)
        if not image_url:
            yield event.plain_result("❌ 没找到图片！请先上传图片")
            return

        if not image_url.startswith(("http://", "https://", "data:")):
            yield event.plain_result("📤 正在上传图片...")
            public_url = _upload_image(image_url)
            if public_url and (public_url.startswith(("http", "data:"))):
                image_url = public_url
            else:
                yield event.plain_result("❌ 无法上传图片到公网")
                return

        yield event.plain_result(f"🎬 正在生成视频「{prompt}」...")

        try:
            task = self.provider.image_to_video(prompt, image_url, model=self.video_model)
            task_id = task.get("task_id", "")
            yield event.plain_result(f"🎬 视频任务已提交，正在生成...")

            for _ in range(60):
                time.sleep(5)
                try:
                    status = self.provider.get_video_status(task_id)
                    if status.get("status") == "completed" and status.get("video"):
                        chain = [Plain("🎬 图生视频完成！"), Video.fromURL(status["video"])]
                        yield event.chain_result(chain)
                        return
                    if status.get("status") in ("failed", "timeout"):
                        yield event.plain_result(f"❌ 视频生成失败: {status.get('error', '未知错误')}")
                        return
                except Exception:
                    pass
            yield event.plain_result("⏰ 视频生成超时，请稍后重试")
        except RuntimeError as e:
            yield event.plain_result(f"❌ {e}")
        except Exception as e:
            yield event.plain_result(f"❌ 出错: {str(e)}")

    # ─── 命令：设置模型 ───

    @filter.command("设置模型")
    async def set_model(self, event: AstrMessageEvent):
        """/设置模型 <模型名> - 切换生图/生视频使用的模型"""
        model_name = _get_prompt(event)
        if not model_name:
            yield event.plain_result(
                f"当前生图模型: {self.image_model}\n"
                f"当前生视频模型: {self.video_model}\n"
                "用法：/设置模型 <模型名>\n"
                "可用模型请用 /生图模型 查看"
            )
            return

        model_name = model_name.strip()
        # 获取有效模型列表
        valid_models = list(self.custom_models)
        try:
            for m in self.provider.list_models():
                valid_models.append(m["id"])
        except Exception:
            pass

        if model_name not in valid_models:
            yield event.plain_result(
                f"❌ 未知模型「{model_name}」\n"
                "可用模型：/生图模型\n"
                "自定义模型请在插件配置 → custom_models 中添加"
            )
            return

        if "video" in model_name.lower():
            self.video_model = model_name
            yield event.plain_result(f"✅ 生视频模型已切换为: {model_name}")
        else:
            self.image_model = model_name
            yield event.plain_result(f"✅ 生图模型已切换为: {model_name}")

    # ─── 命令：查看模型 ───

    @filter.command("生图模型")
    async def list_models(self, event: AstrMessageEvent):
        """查看当前提供商可用的模型"""
        try:
            models = self.provider.list_models()
            msg = f"🤖 [{self._provider_name.upper()}] 可用模型：\n"
            for m in models:
                prefix = {"image": "🖼️", "video": "🎬", "chat": "💬"}.get(m.get("type", ""), "❓")
                msg += f"{prefix} {m['id']}\n"
            msg += f"\n💡 当前生图: {self.image_model}\n"
            msg += f"💡 当前生视频: {self.video_model}"
            yield event.plain_result(msg.strip())
        except Exception as e:
            yield event.plain_result(f"❌ 获取模型列表失败: {e}")

    # ─── 命令：提供商切换 ───

    @filter.command("提供商")
    async def switch_provider(self, event: AstrMessageEvent):
        """/提供商 - 查看/切换 AI 提供商
        用法：
          /提供商         查看当前提供商和可选列表
          /提供商 openai  切换到 OpenAI
          /提供商 agnes   切换到 Agnes AI"""
        name = _get_prompt(event)
        if not name:
            available = get_all_provider_names()
            yield event.plain_result(
                f"🔌 当前提供商: {self._provider_name}\n"
                f"📋 可用提供商: {', '.join(available)}\n"
                "切换：/提供商 <名称>\n"
                f"当前生图模型: {self.image_model}"
            )
            return

        name = name.strip().lower()
        available = get_all_provider_names()
        if name not in available:
            yield event.plain_result(f"❌ 未知提供商「{name}」，可用: {', '.join(available)}")
            return

        # 获取 API Key（可能不同提供商用不同 key）
        api_key = _find_api_key(self.config.get("api_key", ""))

        try:
            new_provider = create_provider(name, api_key)
            self._provider = new_provider
            self._provider_name = name

            # 重置模型为对应提供商的默认
            if name == "agnes":
                self.image_model = "agnes-image-2.1-flash"
                self.video_model = "agnes-video-v2.0"
            elif name == "openai":
                self.image_model = "dall-e-3"
            elif name == "siliconflow":
                self.image_model = "black-forest-labs/FLUX.1-dev"

            # 列出该提供商的模型
            try:
                models = new_provider.list_models()
                model_list = "\n".join(f"  - {m['id']}" for m in models[:10])
                yield event.plain_result(
                    f"✅ 已切换到 {name}\n"
                    f"可用模型：\n{model_list}\n"
                    f"💡 当前生图: {self.image_model}\n"
                    f"💡 当前生视频: {self.video_model}"
                )
            except Exception:
                yield event.plain_result(f"✅ 已切换到 {name}")
        except ValueError as e:
            yield event.plain_result(f"❌ {e}")
        except Exception as e:
            yield event.plain_result(f"❌ 切换失败: {e}")

    # ─── 命令：状态 ───

    @filter.command("agnes")
    async def plugin_status(self, event: AstrMessageEvent):
        """查看 Agnes 插件状态"""
        try:
            models = self.provider.list_models()
            model_count = len(models)
        except Exception:
            model_count = 0

        msg = (
            "📊 Agnes AI 插件状态\n"
            f"├ 提供商: {self._provider_name}\n"
            f"├ 生图模型: {self.image_model}\n"
            f"├ 生视频模型: {self.video_model}\n"
            f"├ 可用模型数: {model_count}\n"
            f"├ API Key: {'✅ 已配置' if _find_api_key(self.config.get('api_key', '')) else '❌ 未配置'}\n"
            f"└ 架构: 直连远端 API（无需本地 Proxy）"
        )
        yield event.plain_result(msg)

    # ─── 生命周期 ───

    async def terminate(self):
        logger.info("[Agnes] 插件卸载")
