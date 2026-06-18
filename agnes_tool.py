"""
Agnes AI 生图/视频/图改图/图生视频工具 - 给 AI Agent 调用的接口
用法:
  python agnes_tool.py image "prompt" [model] [size]              # 文生图
  python agnes_tool.py img2img "prompt" <image_url> [model]      # 图改图
  python agnes_tool.py video "prompt" [model]                     # 文生视频
  python agnes_tool.py img2video "prompt" <image_url> [model]    # 图生视频
"""
import requests
import json
import sys
import time

PROXY_URL = "http://127.0.0.1:1241"

def generate_image(prompt, model="agnes-image-2.1-flash", size="1024x1024"):
    """文生图：生成图片，返回图片URL"""
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    resp = requests.post(f"{PROXY_URL}/v1/images/generations", json=payload, timeout=120)
    if resp.status_code == 200:
        data = resp.json().get("data", [])
        if data:
            return data[0].get("url")
    return f"Error: {resp.text[:200]}"

def img2img(prompt, image_url, model="agnes-image-2.1-flash"):
    """图改图：传入图片+描述，返回修改后的图片URL"""
    payload = {
        "model": model,
        "prompt": prompt,
        "image": image_url,
        "n": 1,
    }
    resp = requests.post(f"{PROXY_URL}/v1/images/generations", json=payload, timeout=120)
    if resp.status_code == 200:
        data = resp.json().get("data", [])
        if data:
            return data[0].get("url")
    return f"Error: {resp.text[:200]}"

def generate_video(prompt, model="agnes-video-v2.0"):
    """文生视频：返回视频URL（异步等待完成）"""
    payload = {
        "model": model,
        "prompt": prompt,
    }
    resp = requests.post(f"{PROXY_URL}/v1/videos", json=payload, timeout=30)
    if resp.status_code != 202:
        return f"Error: {resp.text[:200]}"
    
    task_id = resp.json().get("id")
    print(f"Video task created: {task_id}, waiting...")
    
    for i in range(60):
        time.sleep(5)
        status_resp = requests.get(f"{PROXY_URL}/v1/videos/{task_id}", timeout=10)
        if status_resp.status_code == 200:
            data = status_resp.json()
            status = data.get("status")
            if status == "completed":
                return data.get("video")
            elif status in ("failed", "timeout"):
                return f"Failed: {data.get('error', 'Unknown')}"
    return "Timeout"

def img2video(prompt, image_url, model="agnes-video-v2.0"):
    """图生视频：传入图片+描述，返回视频URL（异步等待完成）"""
    payload = {
        "model": model,
        "prompt": prompt,
        "image": image_url,
    }
    resp = requests.post(f"{PROXY_URL}/v1/videos", json=payload, timeout=30)
    if resp.status_code != 202:
        return f"Error: {resp.text[:200]}"
    
    task_id = resp.json().get("id")
    print(f"Video task created: {task_id}, waiting...")
    
    for i in range(60):
        time.sleep(5)
        status_resp = requests.get(f"{PROXY_URL}/v1/videos/{task_id}", timeout=10)
        if status_resp.status_code == 200:
            data = status_resp.json()
            status = data.get("status")
            if status == "completed":
                return data.get("video")
            elif status in ("failed", "timeout"):
                return f"Failed: {data.get('error', 'Unknown')}"
    return "Timeout"

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法:")
        print("  python agnes_tool.py image \"描述\" [模型] [尺寸]           # 文生图")
        print("  python agnes_tool.py img2img \"描述\" <图片URL> [模型]      # 图改图")
        print("  python agnes_tool.py video \"描述\" [模型]                  # 文生视频")
        print("  python agnes_tool.py img2video \"描述\" <图片URL> [模型]    # 图生视频")
        sys.exit(1)
    
    mode = sys.argv[1]
    prompt = sys.argv[2]
    
    if mode == "image":
        model = sys.argv[3] if len(sys.argv) > 3 else "agnes-image-2.1-flash"
        size = sys.argv[4] if len(sys.argv) > 4 else "1024x1024"
        result = generate_image(prompt, model, size)
        print(f"RESULT_IMAGE_URL={result}")
    elif mode == "img2img":
        image_url = sys.argv[3] if len(sys.argv) > 3 else ""
        model = sys.argv[4] if len(sys.argv) > 4 else "agnes-image-2.1-flash"
        result = img2img(prompt, image_url, model)
        print(f"RESULT_IMAGE_URL={result}")
    elif mode == "video":
        model = sys.argv[3] if len(sys.argv) > 3 else "agnes-video-v2.0"
        result = generate_video(prompt, model)
        print(f"RESULT_VIDEO_URL={result}")
    elif mode == "img2video":
        image_url = sys.argv[3] if len(sys.argv) > 3 else ""
        model = sys.argv[4] if len(sys.argv) > 4 else "agnes-video-v2.0"
        result = img2video(prompt, image_url, model)
        print(f"RESULT_VIDEO_URL={result}")
    else:
        print(f"Unknown mode: {mode}")
