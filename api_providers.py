"""
AI 模型提供商抽象层 v2.1
支持：Agnes AI（原生直连）、OpenAI/DALL-E、SiliconFlow 等
无需本地 Proxy，插件直连远端 API
"""
import os
import json
import time
import logging
import threading
from urllib.parse import urljoin

import requests

logger = logging.getLogger("agnes-provider")


# ============ 基础接口 ============

class BaseProvider:
    """所有提供商必须继承此类"""
    name = ""  # 唯一标识符

    def text_to_image(self, prompt: str, model: str = None, size: str = None, **kwargs) -> dict:
        """文生图，返回 {"url": str} 或抛出异常"""
        raise NotImplementedError

    def image_to_image(self, prompt: str, image_url: str, model: str = None, **kwargs) -> dict:
        """图改图，返回 {"url": str} 或抛出异常"""
        raise NotImplementedError

    def text_to_video(self, prompt: str, model: str = None, **kwargs) -> dict:
        """
        文生视频（异步），返回 {"task_id": str} 或抛出异常
        任务状态通过 get_video_status() 获取
        """
        raise NotImplementedError

    def image_to_video(self, prompt: str, image_url: str, model: str = None, **kwargs) -> dict:
        """图生视频（异步），同上"""
        raise NotImplementedError

    def get_video_status(self, task_id: str) -> dict:
        """查询视频任务状态，返回 {"status": str, "video": str, "error": str}"""
        raise NotImplementedError

    def list_models(self) -> list:
        """返回模型列表 [{"id": str, "type": str}, ...]"""
        raise NotImplementedError


# ============ Agnes AI 原生提供商 ============

class AgnesProvider(BaseProvider):
    """Agnes AI 原生 API（直连 https://apihub.agnes-ai.com/v1/，无需本地 Proxy）"""
    name = "agnes"
    BASE_URL = "https://apihub.agnes-ai.com/v1/"

    # 内置模型列表（远端可能还有更多）
    BUILTIN_MODELS = {
        "agnes-1.5-flash": "chat",
        "agnes-2.0-flash": "chat",
        "agnes-image-2.0-flash": "image",
        "agnes-image-2.1-flash": "image",
        "agnes-video-v2.0": "video",
    }

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._video_tasks = {}  # task_id -> task_info
        self._video_tasks_lock = threading.Lock()

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, json_body: dict = None, timeout: int = 120) -> requests.Response:
        url = urljoin(self.BASE_URL, path.lstrip("/"))
        return requests.request(method, url, headers=self._headers(), json=json_body, timeout=timeout)

    # ─── 文生图 ───

    def text_to_image(self, prompt: str, model: str = "agnes-image-2.1-flash",
                      size: str = "1024x1024", **kwargs) -> dict:
        payload = {"model": model, "prompt": prompt, "n": 1}
        if size:
            payload["size"] = size

        resp = self._request("POST", "images/generations", payload)
        if resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", resp.text[:300])
            raise RuntimeError(f"Agnes 文生图失败 ({resp.status_code}): {err}")

        data = resp.json().get("data", [])
        for img in data:
            if "url" in img:
                return {"url": img["url"]}
            if "b64_json" in img:
                return {"b64_json": img["b64_json"]}
        raise RuntimeError("Agnes 文生图返回为空")

    # ─── 图改图 ───

    def image_to_image(self, prompt: str, image_url: str,
                       model: str = "agnes-image-2.1-flash", **kwargs) -> dict:
        payload = {"model": model, "prompt": prompt, "image": image_url, "n": 1}

        # 多图合成时自动降级到 2.0
        images = kwargs.get("images")
        if images:
            payload["images"] = images
            if model == "agnes-image-2.1-flash":
                payload["model"] = "agnes-image-2.0-flash"

        resp = self._request("POST", "images/generations", payload)
        if resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", resp.text[:300])
            raise RuntimeError(f"Agnes 图改图失败 ({resp.status_code}): {err}")

        data = resp.json().get("data", [])
        for img in data:
            if "url" in img:
                return {"url": img["url"]}
            if "b64_json" in img:
                return {"b64_json": img["b64_json"]}
        raise RuntimeError("Agnes 图改图返回为空")

    # ─── 文生视频 ───

    def text_to_video(self, prompt: str, model: str = "agnes-video-v2.0", **kwargs) -> dict:
        payload = {"model": model, "prompt": prompt}
        resp = self._request("POST", "videos", payload)
        if resp.status_code != 202 and resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", resp.text[:300])
            raise RuntimeError(f"Agnes 创建视频任务失败 ({resp.status_code}): {err}")

        result = resp.json()
        task_id = result.get("task_id") or result.get("id")
        video_id = result.get("video_id")

        with self._video_tasks_lock:
            self._video_tasks[task_id] = {
                "status": "pending", "task_id": task_id, "video_id": video_id,
                "model": model, "prompt": prompt, "created_at": int(time.time()),
            }

        # 启动后台轮询
        threading.Thread(target=self._poll_video, args=(task_id, video_id, model), daemon=True).start()
        return {"task_id": task_id}

    # ─── 图生视频 ───

    def image_to_video(self, prompt: str, image_url: str,
                       model: str = "agnes-video-v2.0", **kwargs) -> dict:
        payload = {"model": model, "prompt": prompt, "image": image_url}
        resp = self._request("POST", "videos", payload)
        if resp.status_code != 202 and resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", resp.text[:300])
            raise RuntimeError(f"Agnes 创建视频任务失败 ({resp.status_code}): {err}")

        result = resp.json()
        task_id = result.get("task_id") or result.get("id")
        video_id = result.get("video_id")

        with self._video_tasks_lock:
            self._video_tasks[task_id] = {
                "status": "pending", "task_id": task_id, "video_id": video_id,
                "model": model, "prompt": prompt, "created_at": int(time.time()),
            }

        threading.Thread(target=self._poll_video, args=(task_id, video_id, model), daemon=True).start()
        return {"task_id": task_id}

    # ─── 视频状态查询 ───

    def get_video_status(self, task_id: str) -> dict:
        """查询视频任务状态"""
        with self._video_tasks_lock:
            task = self._video_tasks.get(task_id)

        if task:
            return {
                "status": task.get("status", "unknown"),
                "video": task.get("video_url", ""),
                "error": task.get("error", ""),
            }

        # fallback: 直接调远端
        try:
            resp = self._request("GET", f"videos/{task_id}", timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": data.get("status", "unknown"),
                    "video": data.get("video_url") or data.get("url", ""),
                    "error": data.get("error", ""),
                }
        except Exception:
            pass
        return {"status": "unknown", "video": "", "error": "任务不存在"}

    # ─── 模型列表 ───

    def list_models(self) -> list:
        """返回模型列表（内置 + 远端）"""
        models = [{"id": mid, "type": t} for mid, t in self.BUILTIN_MODELS.items()]
        try:
            resp = self._request("GET", "models", timeout=10)
            if resp.status_code == 200:
                for m in resp.json().get("data", []):
                    mid = m.get("id", "")
                    if mid and mid not in self.BUILTIN_MODELS:
                        models.append({"id": mid, "type": "unknown"})
        except Exception:
            pass
        return models

    # ─── 后台轮询 ───

    def _poll_video(self, task_id: str, video_id: str, model: str,
                    max_attempts: int = 60, interval: int = 5):
        logger.info(f"[Agnes] 视频轮询开始: {task_id}")
        query_urls = []
        if video_id:
            query_urls.append(
                f"https://apihub.agnes-ai.com/agnesapi?video_id={video_id}&model_name={model}"
            )
        query_urls.append(urljoin(self.BASE_URL, f"videos/{task_id}"))

        for attempt in range(max_attempts):
            time.sleep(interval)
            for url in query_urls:
                try:
                    resp = requests.get(url, headers=self._headers(), timeout=30)
                    if resp.status_code == 200:
                        result = resp.json()
                        status = result.get("status", "").lower()
                        with self._video_tasks_lock:
                            if task_id in self._video_tasks:
                                if status in ("completed", "success", "finished"):
                                    self._video_tasks[task_id]["status"] = "completed"
                                    self._video_tasks[task_id]["video_url"] = (
                                        result.get("video_url") or result.get("url")
                                        or result.get("output") or result.get("remixed_from_video_id")
                                    )
                                    logger.info(f"[Agnes] 视频完成: {task_id}")
                                    return
                                elif status in ("failed", "error"):
                                    self._video_tasks[task_id]["status"] = "failed"
                                    self._video_tasks[task_id]["error"] = result.get("error", "Unknown")
                                    logger.error(f"[Agnes] 视频失败: {task_id}")
                                    return
                                else:
                                    self._video_tasks[task_id]["status"] = status
                        break
                except Exception as e:
                    logger.debug(f"[Agnes] 轮询异常: {e}")

        with self._video_tasks_lock:
            if task_id in self._video_tasks:
                if self._video_tasks[task_id]["status"] not in ("completed", "failed"):
                    self._video_tasks[task_id]["status"] = "timeout"
                    self._video_tasks[task_id]["error"] = "轮询超时"
        logger.warning(f"[Agnes] 视频轮询超时: {task_id}")


# ============ OpenAI / DALL-E 提供商 ============

class OpenAIProvider(BaseProvider):
    """OpenAI DALL-E 3 以及兼容 API"""
    name = "openai"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def text_to_image(self, prompt: str, model: str = "dall-e-3",
                      size: str = "1024x1024", **kwargs) -> dict:
        payload = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": kwargs.get("quality", "standard"),
        }
        resp = requests.post(
            f"{self.base_url}/images/generations",
            headers=self._headers(), json=payload, timeout=120
        )
        if resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", resp.text[:300])
            raise RuntimeError(f"OpenAI 文生图失败 ({resp.status_code}): {err}")
        data = resp.json().get("data", [{}])[0]
        if "url" in data:
            return {"url": data["url"]}
        if "b64_json" in data:
            return {"b64_json": data["b64_json"]}
        raise RuntimeError("OpenAI 返回数据异常")

    def image_to_image(self, prompt: str, image_url: str,
                       model: str = "dall-e-2", **kwargs) -> dict:
        # DALL-E 2 支持图改图；DALL-E 3 不支持
        payload = {
            "model": model,
            "prompt": prompt,
            "image": image_url,
            "n": 1,
            "size": kwargs.get("size", "1024x1024"),
        }
        resp = requests.post(
            f"{self.base_url}/images/edits",
            headers=self._headers(), json=payload, timeout=120
        )
        if resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", resp.text[:300])
            raise RuntimeError(f"OpenAI 图改图失败 ({resp.status_code}): {err}")
        data = resp.json().get("data", [{}])[0]
        return {"url": data.get("url") or data.get("b64_json", "")}

    def list_models(self) -> list:
        return [
            {"id": "dall-e-3", "type": "image"},
            {"id": "dall-e-2", "type": "image"},
        ]

    def text_to_video(self, **kwargs):
        raise RuntimeError("OpenAI 暂不支持文生视频，请切换为 Agnes 提供商")

    def image_to_video(self, **kwargs):
        raise RuntimeError("OpenAI 暂不支持图生视频，请切换为 Agnes 提供商")

    def get_video_status(self, task_id: str) -> dict:
        return {"status": "unsupported", "video": "", "error": "OpenAI 不支持视频"}


# ============ SiliconFlow 提供商（开源模型） ============

class SiliconFlowProvider(BaseProvider):
    """SiliconFlow API - 开源图像/视频模型"""
    name = "siliconflow"

    def __init__(self, api_key: str, base_url: str = "https://api.siliconflow.cn/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def text_to_image(self, prompt: str, model: str = "black-forest-labs/FLUX.1-dev",
                      size: str = "1024x1024", **kwargs) -> dict:
        payload = {"model": model, "prompt": prompt, "n": 1}
        resp = requests.post(
            f"{self.base_url}/images/generations",
            headers=self._headers(), json=payload, timeout=120
        )
        if resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", resp.text[:300])
            raise RuntimeError(f"SiliconFlow 文生图失败 ({resp.status_code}): {err}")
        data = resp.json().get("data", [{}])[0]
        url = data.get("url") or data.get("image_url", "")
        if url:
            return {"url": url}
        raise RuntimeError("SiliconFlow 返回数据异常")

    def list_models(self) -> list:
        return [
            {"id": "black-forest-labs/FLUX.1-dev", "type": "image"},
            {"id": "stabilityai/stable-diffusion-3-5-large", "type": "image"},
            {"id": "black-forest-labs/FLUX.1-schnell", "type": "image"},
        ]

    def image_to_image(self, **kwargs):
        raise RuntimeError("SiliconFlow 图改图暂未实现")

    def text_to_video(self, **kwargs):
        raise RuntimeError("SiliconFlow 文生视频暂未实现")

    def image_to_video(self, **kwargs):
        raise RuntimeError("SiliconFlow 图生视频暂未实现")

    def get_video_status(self, **kwargs):
        return {"status": "unsupported", "video": "", "error": "SiliconFlow 暂不支持视频"}


# ============ 提供商工厂 ============

PROVIDER_MAP = {
    "agnes": AgnesProvider,
    "openai": OpenAIProvider,
    "siliconflow": SiliconFlowProvider,
}


def create_provider(name: str, api_key: str, **kwargs) -> BaseProvider:
    """根据名称创建提供商实例"""
    cls = PROVIDER_MAP.get(name.lower())
    if not cls:
        raise ValueError(f"未知提供商: {name}，可选: {list(PROVIDER_MAP.keys())}")
    return cls(api_key, **kwargs)


def get_all_provider_names() -> list:
    """获取所有可用提供商名称"""
    return list(PROVIDER_MAP.keys())
