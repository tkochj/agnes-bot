"""
Agnes AI 生图/生视频插件 v2.0
- 自动发现 API Key（从 AstrBot 的 provider 配置中自动读取）
- 自动启动/管理 Proxy
- 提供 /画图、/生视频 等指令
"""
import os
import sys
import json
import time
import re
import subprocess
import glob

import requests
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image, Video
import base64

PROXY_PORT = 1241
PROXY_URL = f"http://127.0.0.1:{PROXY_PORT}"

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_SCRIPT = os.path.join(PLUGIN_DIR, "agnes_proxy.py")


def _get_command_prompt(event: AstrMessageEvent) -> str:
    """兼容不同平台获取命令参数（QQ 平台没有 get_args）"""
    try:
        args = event.get_args()
        if args:
            return " ".join(args)
    except AttributeError:
        pass
    # 从消息文本中提取命令参数
    raw = getattr(event, 'message_str', '') or ''
    prompt = re.sub(r"^/\S*\s*", "", raw, count=1).strip()
    return prompt


def _auto_discover_api_key(configured_key=""):
    """
    自动发现 Agnes API Key，优先级：
    1. 插件配置中手动填入的 api_key
    2. AstrBot provider 配置中指向 127.0.0.1:1241 的 key
    3. 环境变量 AGNES_API_KEY
    """
    if configured_key:
        logger.info("[Agnes] 使用插件配置的 api_key")
        return configured_key

    try:
        config_paths = [
            os.path.abspath(os.path.join(PLUGIN_DIR, "..", "..", "..", "..", "cmd_config.json")),
            os.path.abspath(os.path.join(PLUGIN_DIR, "..", "..", "..", "cmd_config.json")),
            os.path.abspath(os.path.join(PLUGIN_DIR, "..", "..", "..", "..", "..", "cmd_config.json")),
        ]
        layout_paths = glob.glob(
            os.path.abspath(os.path.join(PLUGIN_DIR, "..", "..", "..", "*_layout*config*.json"))
        )
        config_paths.extend(layout_paths)

        for cfg_path in config_paths:
            if not os.path.exists(cfg_path):
                continue
            try:
                with open(cfg_path, "r", encoding="utf-8-sig") as f:
                    config = json.load(f)
            except Exception:
                continue

            def _deep_find(obj, depth=0):
                if depth > 8:
                    return None
                if isinstance(obj, dict):
                    api_base = obj.get("api_base", "")
                    if "127.0.0.1" in api_base and str(PROXY_PORT) in api_base:
                        key_list = obj.get("key", [])
                        if isinstance(key_list, list) and key_list and key_list[0]:
                            return key_list[0].strip()
                    for v in obj.values():
                        r = _deep_find(v, depth + 1)
                        if r:
                            return r
                elif isinstance(obj, list):
                    for item in obj:
                        r = _deep_find(item, depth + 1)
                        if r:
                            return r
                return None

            found_key = _deep_find(config)
            if found_key:
                logger.info("[Agnes] 自动发现 API Key ✓")
                return found_key

    except Exception as e:
        logger.warning(f"[Agnes] 自动发现 Key 时出错: {e}")

    env_key = os.environ.get("AGNES_API_KEY", "")
    if env_key:
        logger.info("[Agnes] 使用环境变量 AGNES_API_KEY")
        return env_key

    logger.warning("[Agnes] 未找到 API Key，请配置后再使用")
    return ""


def _start_proxy_background(api_key):
    """在后台启动 Agnes Proxy"""
    try:
        resp = requests.get(f"{PROXY_URL}/health", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("api_key_configured"):
                logger.info("[Agnes] Proxy 已在运行 ✓")
                return True
    except:
        pass

    try:
        env = os.environ.copy()
        if api_key:
            env["AGNES_API_KEY"] = api_key

        python_exe = sys.executable
        proc = subprocess.Popen(
            [python_exe, PROXY_SCRIPT],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )

        for i in range(10):
            time.sleep(1)
            try:
                resp = requests.get(f"{PROXY_URL}/health", timeout=2)
                if resp.status_code == 200:
                    has_key = resp.json().get("api_key_configured", False)
                    logger.info(f"[Agnes] Proxy 启动完成 ✓ (PID: {proc.pid}, Key: {'已配置' if has_key else '未配置'})")
                    return True
            except:
                pass

        logger.error("[Agnes] Proxy 启动超时")
        return False
    except Exception as e:
        logger.error(f"[Agnes] 启动 Proxy 失败: {e}")
        return False


class AgnesPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.last_image_url = None  # 缓存用户最后发送的图片URL
        self.image_model = self.config.get("image_model", "agnes-image-2.1-flash")
        self.video_model = self.config.get("video_model", "agnes-video-v2.0")
        api_key = _auto_discover_api_key(self.config.get("api_key", ""))
        _start_proxy_background(api_key)

    @filter.command("缓存图片")
    async def cache_image_manually(self, event: AstrMessageEvent):
        """/缓存图片 - 手动将当前消息中的图片缓存用于后续 /改图"""
        img_url = self._get_image_url_from_event(event)
        if img_url:
            self.last_image_url = img_url
            yield event.plain_result(f"✅ 已缓存图片: {img_url[:60]}...")
        else:
            yield event.plain_result("❌ 当前消息中没有找到图片")

    @filter.command("画图")
    async def draw_image(self, event: AstrMessageEvent):
        """/画图 <描述> - 用AI生成图片"""
        prompt = _get_command_prompt(event)
        if not prompt:
            yield event.plain_result(
                "请描述你想画的内容~ 例如：\n"
                "/画图 一只穿着西装的猫猫在办公桌前喝咖啡"
            )
            return

        yield event.plain_result(f"🎨 正在画「{prompt}」...请稍等")

        try:
            resp = requests.post(
                f"{PROXY_URL}/v1/images/generations",
                json={
                    "model": "agnes-image-2.1-flash",
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1024",
                },
                timeout=120,
            )

            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data and "url" in data[0]:
                    img_url = data[0]["url"]
                    chain = [Image(file=img_url, url=img_url)]
                    yield event.chain_result(chain)
                else:
                    yield event.plain_result("生成成功但未获取到图片URL")
            else:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:200])
                yield event.plain_result(f"❌ 生成失败: {err_msg}")
        except requests.exceptions.ConnectionError:
            yield event.plain_result("❌ 代理未运行，请联系管理员检查 Agnes Proxy 状态")
        except Exception as e:
            yield event.plain_result(f"❌ 出错: {str(e)}")

    @filter.command("生视频")
    async def generate_video(self, event: AstrMessageEvent):
        """/生视频 <描述> - 用AI生成视频"""
        prompt = _get_command_prompt(event)
        if not prompt:
            yield event.plain_result(
                "请描述视频内容~ 例如：\n"
                "/生视频 一条龙在云中飞舞"
            )
            return

        yield event.plain_result(f"🎬 正在生成视频「{prompt}」...可能需要 1-2 分钟")

        try:
            resp = requests.post(
                f"{PROXY_URL}/v1/videos",
                json={
                    "model": "agnes-video-v2.0",
                    "prompt": prompt,
                },
                timeout=30,
            )

            if resp.status_code != 202:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:200])
                yield event.plain_result(f"❌ 创建视频任务失败: {err_msg}")
                return

            task_id = resp.json().get("id")
            yield event.plain_result(f"🎬 视频任务已提交 (ID: {task_id[:12]}...)，正在生成中...")

            start_ts = time.time()
            last_progress_msg = 0
            for i in range(60):
                time.sleep(5)
                try:
                    status_resp = requests.get(f"{PROXY_URL}/v1/videos/{task_id}", timeout=10)
                    if status_resp.status_code == 200:
                        data = status_resp.json()
                        status = data.get("status")
                        elapsed = int(time.time() - start_ts)
                        if elapsed - last_progress_msg >= 30:
                            yield event.plain_result(f"⏳ 视频生成中... 已等待 {elapsed} 秒")
                            last_progress_msg = elapsed
                        if status == "completed" and data.get("video"):
                            video_url = data["video"]
                            chain = [Video.fromURL(video_url)]
                            yield event.chain_result(chain)
                            return
                        elif status in ("failed", "timeout"):
                            yield event.plain_result(f"❌ 视频生成失败: {data.get('error', '未知错误')}")
                            return
                except:
                    pass

            yield event.plain_result("⏰ 视频生成超时（>5分钟），请稍后重试")
        except requests.exceptions.ConnectionError:
            yield event.plain_result("❌ 代理未运行，请联系管理员检查 Agnes Proxy 状态")
        except Exception as e:
            yield event.plain_result(f"❌ 出错: {str(e)}")

    @filter.command("设置模型")
    async def set_model(self, event: AstrMessageEvent):
        """/设置模型 <模型名> - 切换生图/生视频使用的模型"""
        prompt = _get_command_prompt(event)
        if not prompt:
            yield event.plain_result(
                "用法：/设置模型 <模型名>\n"
                "例如：/设置模型 agnes-image-2.1-flash\n"
                f"当前生图模型: {self.image_model}\n"
                f"当前生视频模型: {self.video_model}\n"
                "可用模型请用 /生图模型 查看"
            )
            return
        model_name = prompt.strip()
        try:
            resp = requests.get(f"{PROXY_URL}/v1/models", timeout=5)
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                valid_ids = [m.get("id", "") for m in models]
                if model_name not in valid_ids:
                    mlist = "\n".join(f"  - {m}" for m in valid_ids[:10])
                    yield event.plain_result(
                        f"❌ 未知模型「{model_name}」\n"
                        f"可用模型：\n{mlist}"
                    )
                    return
        except:
            pass
        if "video" in model_name.lower():
            self.video_model = model_name
            yield event.plain_result(f"✅ 生视频模型已切换为: {model_name}")
        else:
            self.image_model = model_name
            yield event.plain_result(f"✅ 生图模型已切换为: {model_name}")

    @filter.command("生图模型")
    async def list_models(self, event: AstrMessageEvent):
        """查看可用的生图/生视频模型"""
        try:
            resp = requests.get(f"{PROXY_URL}/v1/models", timeout=5)
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                msg = "🤖 可用模型：\n"
                for m in models:
                    mid = m["id"]
                    if "image" in mid:
                        msg += f"🖼️ {mid}\n"
                    elif "video" in mid:
                        msg += f"🎬 {mid}\n"
                    else:
                        msg += f"💬 {mid}\n"
                yield event.plain_result(msg.strip())
            else:
                yield event.plain_result("❌ 获取模型列表失败")
        except requests.exceptions.ConnectionError:
            yield event.plain_result("❌ 代理未运行，Proxy 可能未启动")
        except Exception as e:
            yield event.plain_result(f"❌ 出错: {str(e)}")

    @filter.command("agnes状态")
    async def proxy_status(self, event: AstrMessageEvent):
        """查看 Agnes Proxy 运行状态"""
        try:
            resp = requests.get(f"{PROXY_URL}/health", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                key_status = "✅ 已配置" if data.get("api_key_configured") else "❌ 未配置"
                msg = (
                    "📊 Agnes AI Proxy 状态\n"
                    f"├ 状态: ✅ 运行中\n"
                    f"├ API Key: {key_status}\n"
                    f"└ 可用模型: {', '.join(data.get('models', []))}"
                )
                yield event.plain_result(msg)
            else:
                yield event.plain_result("❌ Proxy 异常")
        except requests.exceptions.ConnectionError:
            yield event.plain_result("❌ Agnes Proxy 未运行")

    def _upload_to_public_url(self, file_path: str) -> str:
        """使用 agnes-ai-cli 将本地图片上传为公网可访问的 URL"""
        if not file_path or not os.path.exists(file_path):
            return file_path
        # 已经是 HTTP URL，无需上传
        if file_path.startswith('http://') or file_path.startswith('https://'):
            return file_path
        try:
            logger.info(f"[Agnes] 上传本地图片到公网: {file_path}")
            result = subprocess.run(
                ["npx", "-y", "agnes-ai-cli@^0.1.0", "media", "url", file_path],
                capture_output=True, text=True, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                logger.info(f"[Agnes] 上传成功: {url}")
                return url
            else:
                logger.error(f"[Agnes] 上传失败: {result.stderr[:200]}")
                # 尝试用 Base64 内嵌
                return self._file_to_data_uri(file_path)
        except Exception as e:
            logger.error(f"[Agnes] 上传异常: {e}")
            return self._file_to_data_uri(file_path)
    
    def _file_to_data_uri(self, file_path: str) -> str:
        """将本地图片转为 data URI（某些 API 支持）"""
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            import base64
            ext = os.path.splitext(file_path)[1].lower()
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "gif": "image/gif", "webp": "image/webp"}.get(ext.lstrip("."), "image/jpeg")
            b64 = base64.b64encode(data).decode()
            return f"data:{mime};base64,{b64}"
        except:
            return file_path

    def _get_image_url_from_event(self, event: AstrMessageEvent, prompt: str = "") -> str:
        """从消息中提取第一张图片的 URL
        优先级: 当前消息组件 > 消息文本中的URL > prompt中的URL > 缓存的上次图片
        """
        img_url = None

        # 1. 从当前消息的组件中提取
        try:
            msg_chain = event.get_messages()
            for comp in msg_chain:
                if hasattr(comp, 'type') and comp.type == 'image':
                    img_url = comp.url or comp.file
                    break
                if hasattr(comp, 'url') and comp.url:
                    img_url = comp.url
                    break
                if hasattr(comp, 'file') and comp.file:
                    img_url = comp.file
                    break
        except:
            pass

        # 2. 从原始消息文本中提取图片 URL
        if not img_url:
            raw = getattr(event, 'message_str', '') or ''
            urls = re.findall(r'https?://[^\s]+\.(?:jpg|jpeg|png|gif|webp|bmp)', raw, re.IGNORECASE)
            if urls:
                img_url = urls[0]

        # 3. 从 prompt（命令参数）中提取 URL（用户可能把图片链接写在命令里）
        if not img_url and prompt:
            urls = re.findall(r'https?://[^\s]+\.(?:jpg|jpeg|png|gif|webp|bmp)', prompt, re.IGNORECASE)
            if urls:
                img_url = urls[0]

        # 4. 如果当前消息没有图片，用缓存的上次图片
        if not img_url and self.last_image_url:
            img_url = self.last_image_url

        # 5. 检查工作区缓存文件（由助手上传后写入）
        if not img_url:
            try:
                cache_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                    "data", "workspaces"
                )
                # 找到最新的缓存文件
                for root, dirs, files in os.walk(cache_path):
                    for f in files:
                        if f == "last_image_url.txt":
                            cache_file = os.path.join(root, f)
                            with open(cache_file, 'r', encoding='utf-8') as cf:
                                cached = cf.read().strip()
                                if cached:
                                    img_url = cached
                                    logger.info(f"[Agnes] 从工作区缓存读取图片: {img_url[:60]}...")
                            break
                    if img_url:
                        break
            except Exception as e:
                logger.debug(f"[Agnes] 读取缓存文件失败: {e}")

        # 6. 缓存本次找到的图片
        if img_url:
            self.last_image_url = img_url

        return img_url

    def _on_any_message(self, event: AstrMessageEvent):
        """拦截任意消息，缓存其中的图片 URL"""
        try:
            msg_chain = event.get_messages()
            for comp in msg_chain:
                if hasattr(comp, 'type') and comp.type == 'image':
                    url = comp.url or comp.file
                    if url:
                        self.last_image_url = url
                        logger.debug(f"[Agnes] 缓存图片: {url[:60]}...")
                        return
                if hasattr(comp, 'url') and comp.url:
                    self.last_image_url = comp.url
                    return
                if hasattr(comp, 'file') and comp.file:
                    self.last_image_url = comp.file
                    return
        except:
            pass

    @filter.command("改图")
    async def edit_image(self, event: AstrMessageEvent):
        """/改图 <描述> - 基于上传的图片进行 AI 改图
        用法：先发一张图片，再发 /改图 把这只猫换成蜡笔涂鸦风格"""
        prompt = _get_command_prompt(event)
        if not prompt:
            yield event.plain_result("请描述你想怎么改图~\n先发一张图片，然后：\n/改图 把这只猫换成蜡笔涂鸦风格，粉色蓝色为主")
            return

        image_url = self._get_image_url_from_event(event, prompt)
        if not image_url:
            yield event.plain_result(
                "❌ 没找到图片！请先上传图片，再使用 /改图 命令~"
            )
            return

        yield event.plain_result(f"🎨 正在改图「{prompt}」...请稍等")

        try:
            resp = requests.post(
                f"{PROXY_URL}/v1/images/generations",
                json={
                    "model": "agnes-image-2.1-flash",
                    "prompt": prompt,
                    "image": image_url,
                    "n": 1,
                },
                timeout=120,
            )

            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data and "url" in data[0]:
                    img_url = data[0]["url"]
                    chain = [
                        Plain("🔄 改图完成！"),
                        Image(file=img_url, url=img_url),
                    ]
                    yield event.chain_result(chain)
                else:
                    yield event.plain_result("改图成功但未获取到图片URL")
            else:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:200])
                yield event.plain_result(f"❌ 改图失败: {err_msg}")
        except requests.exceptions.ConnectionError:
            yield event.plain_result("❌ 代理未运行")
        except Exception as e:
            yield event.plain_result(f"❌ 出错: {str(e)}")

    @filter.command("图生视频")
    async def image_to_video(self, event: AstrMessageEvent):
        """/图生视频 <描述> - 基于上传的图片生成视频
        用法：先发一张图片，再发 /图生视频 让画面动起来"""
        prompt = _get_command_prompt(event)
        if not prompt:
            yield event.plain_result(
                "请描述视频效果~\n先发一张图片，然后：\n/图生视频 画面缓缓推近，叶子微微飘动"
            )
            return

        image_url = self._get_image_url_from_event(event, prompt)
        if not image_url:
            yield event.plain_result("❌ 没找到图片！请先上传图片~")
            return

        # 如果是本地文件，上传到公网可访问的 URL
        if not image_url.startswith('http://') and not image_url.startswith('https://') and not image_url.startswith('data:'):
            yield event.plain_result(f"📤 正在上传本地图片到公网...")
            public_url = self._upload_to_public_url(image_url)
            if public_url and (public_url.startswith('http') or public_url.startswith('data:')):
                image_url = public_url
            else:
                yield event.plain_result(f"❌ 无法将本地图片转为公网 URL")
                return

        yield event.plain_result(f"🎬 正在基于图片生成视频「{prompt}」...可能需要 1-2 分钟")

        try:
            resp = requests.post(
                f"{PROXY_URL}/v1/videos",
                json={
                    "model": "agnes-video-v2.0",
                    "prompt": prompt,
                    "image": image_url,
                },
                timeout=30,
            )

            if resp.status_code != 202:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:200])
                yield event.plain_result(f"❌ 创建视频任务失败: {err_msg}")
                return

            task_id = resp.json().get("id")
            yield event.plain_result(f"🎬 视频任务已提交，正在生成中...")

            for i in range(60):
                time.sleep(5)
                try:
                    status_resp = requests.get(f"{PROXY_URL}/v1/videos/{task_id}", timeout=10)
                    if status_resp.status_code == 200:
                        data = status_resp.json()
                        status = data.get("status")
                        if status == "completed" and data.get("video"):
                            video_url = data["video"]
                            chain = [Plain("🎬 图生视频完成！"), Video.fromURL(video_url)]
                            yield event.chain_result(chain)
                            return
                        elif status in ("failed", "timeout"):
                            yield event.plain_result(f"❌ 视频生成失败: {data.get('error', '未知错误')}")
                            return
                except:
                    pass

            yield event.plain_result("⏰ 视频生成超时，请稍后重试")
        except requests.exceptions.ConnectionError:
            yield event.plain_result("❌ 代理未运行")
        except Exception as e:
            yield event.plain_result(f"❌ 出错: {str(e)}")

    async def terminate(self):
        """插件卸载时清理"""
        logger.info("[Agnes] 插件卸载，Proxy 将继续保持运行")
