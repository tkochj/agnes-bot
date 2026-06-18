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
        api_key = _auto_discover_api_key(self.config.get("api_key", ""))
        _start_proxy_background(api_key)

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
                    chain = [Image.from_url(img_url)]
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
                            chain = [Video.from_url(video_url)]
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

    async def terminate(self):
        """插件卸载时清理"""
        logger.info("[Agnes] 插件卸载，Proxy 将继续保持运行")
