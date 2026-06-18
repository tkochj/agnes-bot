"""
Agnes AI API Proxy v2.0 - 将 Agnes AI 的非标准 API 转换为 OpenAI 兼容格式
支持: 文本、图片生成、视频生成（异步轮询）

自动从 AstrBot cmd_config.json 中读取 API Key，也支持环境变量 AGNES_API_KEY。
"""
import os
import sys
import json
import time
import glob
import logging
import threading
from urllib.parse import urljoin

import requests
from flask import Flask, request, jsonify, Response, stream_with_context

# ============ 配置 ============
PROXY_PORT = 1241
PROXY_HOST = "0.0.0.0"
AGNES_BASE_URL = "https://apihub.agnes-ai.com/v1/"

# ============ 日志 ============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agnes-proxy")

# ============ 自动发现 API Key ============
def _discover_api_key():
    """自动发现 API Key：环境变量 > cmd_config.json > 提示"""
    # 1. 环境变量
    env_key = os.environ.get("AGNES_API_KEY", "").strip()
    if env_key:
        return env_key

    # 2. 搜索 cmd_config.json
    search_roots = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    ]
    seen = set()
    for root in search_roots:
        for pattern in ["cmd_config.json", "*_layout*.json"]:
            for fp in glob.glob(os.path.join(root, "**", pattern), recursive=True):
                if fp in seen:
                    continue
                seen.add(fp)
                try:
                    with open(fp, "r", encoding="utf-8-sig") as f:
                        cfg = json.load(f)
                except Exception:
                    continue

                # 遍历配置，找指向本机 1241 端口的 key
                def _find_key(obj, depth=0):
                    if depth > 8:
                        return None
                    if isinstance(obj, dict):
                        api_base = obj.get("api_base", "")
                        if "127.0.0.1" in api_base and str(PROXY_PORT) in api_base:
                            key_list = obj.get("key", [])
                            if isinstance(key_list, list) and key_list and key_list[0]:
                                return key_list[0].strip()
                        for v in obj.values():
                            result = _find_key(v, depth + 1)
                            if result:
                                return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = _find_key(item, depth + 1)
                            if result:
                                return result
                    return None

                key = _find_key(cfg)
                if key:
                    logger.info(f"从配置文件自动读取 API Key ✓")
                    return key
    return ""

AGNES_API_KEY = _discover_api_key()

# ============ Flask App ============
app = Flask(__name__)
video_tasks = {}
video_tasks_lock = threading.Lock()


def get_agnes_headers():
    headers = {"Content-Type": "application/json"}
    if AGNES_API_KEY:
        headers["Authorization"] = f"Bearer {AGNES_API_KEY}"
    return headers


MODELS = {
    "agnes-1.5-flash": {"type": "chat"},
    "agnes-2.0-flash": {"type": "chat"},
    "agnes-image-2.0-flash": {"type": "image"},
    "agnes-image-2.1-flash": {"type": "image"},
    "agnes-video-v2.0": {"type": "video"},
}


# ============ API 路由 ============

@app.route("/v1/models", methods=["GET"])
def list_models():
    model_list = [
        {"id": mid, "object": "model", "created": 1700000000, "owned_by": "agnes-ai"}
        for mid in MODELS
    ]
    return jsonify({"object": "list", "data": model_list})


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    data = request.json
    model = data.get("model", "agnes-2.0-flash")
    logger.info(f"[Chat] 模型: {model}")

    payload = {
        "model": model,
        "messages": data.get("messages", []),
        "stream": data.get("stream", False),
        "temperature": data.get("temperature", 0.7),
        "max_tokens": data.get("max_tokens", 4096),
    }

    resp = requests.post(
        urljoin(AGNES_BASE_URL, "chat/completions"),
        headers=get_agnes_headers(),
        json=payload,
        stream=payload["stream"],
        timeout=120,
    )

    if payload["stream"]:
        def generate():
            for line in resp.iter_lines():
                if line:
                    yield line.decode("utf-8") + "\n"
        return Response(
            stream_with_context(generate()),
            content_type=resp.headers.get("content-type", "text/event-stream"),
        )
    return jsonify(resp.json()), resp.status_code


@app.route("/v1/images/generations", methods=["POST"])
def image_generations():
    data = request.json
    model = data.get("model", "agnes-image-2.1-flash")
    prompt = data.get("prompt", "")
    n = data.get("n", 1)
    size = data.get("size", "1024x1024")

    # 构建请求体
    payload = {"model": model, "prompt": prompt, "n": n}

    # 图改图支持：传入 image URL 或本地图片路径
    image = data.get("image", "")
    if image:
        payload["image"] = image
        logger.info(f"[Image] 图改图模式 | {model} | {prompt[:60]}...")
    else:
        logger.info(f"[Image] 文生图模式 | {model} | {prompt[:60]}...")

    # 支持多图合成（image 传数组）
    images = data.get("images", [])
    if images:
        payload["images"] = images
        logger.info(f"[Image] 多图模式 | {model}")
        # 如果有 images 数组，model 强制用 2.0
        if model == "agnes-image-2.1-flash":
            payload["model"] = "agnes-image-2.0-flash"
            model = "agnes-image-2.0-flash"
            logger.info(f"[Image] 多图合成自动切换到: {model}")

    resp = requests.post(
        urljoin(AGNES_BASE_URL, "images/generations"),
        headers=get_agnes_headers(),
        json=payload,
        timeout=120,
    )

    if resp.status_code != 200:
        logger.error(f"[Image] 错误: {resp.text[:200]}")
        return jsonify({"error": {"message": resp.text[:500], "type": "api_error"}}), resp.status_code

    result = resp.json()
    openai_result = {"created": int(time.time()), "data": []}
    for img in result.get("data", []):
        if "url" in img:
            openai_result["data"].append({"url": img["url"]})
        elif "b64_json" in img:
            openai_result["data"].append({"b64_json": img["b64_json"]})
    return jsonify(openai_result)


@app.route("/v1/videos", methods=["POST"])
def create_video():
    data = request.json
    model = data.get("model", "agnes-video-v2.0")
    prompt = data.get("prompt", data.get("input", ""))
    logger.info(f"[Video] 创建任务 | {model} | {prompt[:60]}...")

    payload = {"model": model, "prompt": prompt}
    if "image" in data:
        payload["image"] = data["image"]

    resp = requests.post(
        urljoin(AGNES_BASE_URL, "videos"),
        headers=get_agnes_headers(),
        json=payload,
        timeout=120,
    )

    if resp.status_code != 200:
        logger.error(f"[Video] 创建任务错误: {resp.text[:200]}")
        return jsonify({"error": {"message": resp.text[:500], "type": "api_error"}}), resp.status_code

    result = resp.json()
    task_id = result.get("task_id") or result.get("id")
    video_id = result.get("video_id")

    with video_tasks_lock:
        video_tasks[task_id] = {
            "status": "pending",
            "task_id": task_id,
            "video_id": video_id,
            "model": model,
            "prompt": prompt,
            "created_at": int(time.time()),
        }

    threading.Thread(target=poll_video_task, args=(task_id, video_id, model), daemon=True).start()

    return jsonify({
        "id": task_id,
        "object": "video",
        "status": "pending",
        "created_at": int(time.time()),
    }), 202


@app.route("/v1/videos/<task_id>", methods=["GET"])
def get_video_status(task_id):
    with video_tasks_lock:
        task = video_tasks.get(task_id)

    if not task:
        resp = requests.get(
            urljoin(AGNES_BASE_URL, f"videos/{task_id}"),
            headers=get_agnes_headers(),
            timeout=30,
        )
        if resp.status_code == 200:
            result = resp.json()
            return jsonify({
                "id": task_id,
                "object": "video",
                "status": result.get("status", "unknown"),
                "video": result.get("video_url") or result.get("url"),
            })
        return jsonify({"error": "Task not found"}), 404

    response = {"id": task_id, "object": "video", "status": task["status"]}
    if task.get("video_url"):
        response["video"] = task["video_url"]
    if task.get("error"):
        response["error"] = task["error"]
    return jsonify(response)


def poll_video_task(task_id, video_id, model, max_attempts=60, interval=5):
    logger.info(f"[Video] 轮询开始: {task_id}")
    query_urls = []
    if video_id:
        query_urls.append(
            f"https://apihub.agnes-ai.com/agnesapi?video_id={video_id}&model_name={model}"
        )
    query_urls.append(urljoin(AGNES_BASE_URL, f"videos/{task_id}"))

    for attempt in range(max_attempts):
        time.sleep(interval)
        for url in query_urls:
            try:
                resp = requests.get(url, headers=get_agnes_headers(), timeout=30)
                if resp.status_code == 200:
                    result = resp.json()
                    status = result.get("status", "").lower()
                    with video_tasks_lock:
                        if task_id in video_tasks:
                            if status in ("completed", "success", "finished"):
                                video_tasks[task_id]["status"] = "completed"
                                video_tasks[task_id]["video_url"] = (
                                    result.get("video_url")
                                    or result.get("url")
                                    or result.get("output")
                                    or result.get("remixed_from_video_id")
                                )
                                logger.info(f"[Video] 完成: {task_id}")
                                return
                            elif status in ("failed", "error"):
                                video_tasks[task_id]["status"] = "failed"
                                video_tasks[task_id]["error"] = result.get("error", "Unknown")
                                logger.error(f"[Video] 失败: {task_id}")
                                return
                            else:
                                video_tasks[task_id]["status"] = status
                    break
            except Exception as e:
                logger.debug(f"[Video] 轮询失败: {e}")

    with video_tasks_lock:
        if task_id in video_tasks and video_tasks[task_id]["status"] not in ("completed", "failed"):
            video_tasks[task_id]["status"] = "timeout"
            video_tasks[task_id]["error"] = "Polling timed out"
    logger.warning(f"[Video] 轮询超时: {task_id}")


# ============ 健康检查 & 首页 ============

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "ok",
        "proxy": "Agnes AI Proxy (OpenAI Compatible)",
        "version": "2.0.0",
        "models": list(MODELS.keys()),
        "api_key_configured": bool(AGNES_API_KEY),
    })


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "message": "Agnes AI Proxy - OpenAI Compatible API Gateway",
        "docs": "/health",
        "endpoints": {
            "models": "GET /v1/models",
            "chat": "POST /v1/chat/completions",
            "image": "POST /v1/images/generations",
            "video_create": "POST /v1/videos",
            "video_status": "GET /v1/videos/<task_id>",
        },
    })


# ============ 启动 ============

def print_banner():
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║       Agnes AI Proxy v2.0               ║")
    print("  ║       OpenAI Compatible API Gateway      ║")
    print("  ╠══════════════════════════════════════════╣")
    print(f"  ║  监听:  http://{PROXY_HOST}:{PROXY_PORT}            ║")
    if AGNES_API_KEY:
        print(f"  ║  ✅ API Key: 已自动配置                    ║")
    else:
        print(f"  ║  ❌ API Key: 未配置                        ║")
        print(f"  ║  请设置环境变量 AGNES_API_KEY              ║")
        print(f"  ║  或在 cmd_config.json 中配置               ║")
    print(f"  ║  模型: {', '.join(MODELS.keys()):<33} ║")
    print("  ╚══════════════════════════════════════════╝")
    print()


if __name__ == "__main__":
    print_banner()
    app.run(host=PROXY_HOST, port=PROXY_PORT, debug=False, threaded=True)
