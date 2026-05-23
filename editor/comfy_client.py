"""ComfyUI API client + WAN 2.2 5B (GGUF) 그래프 빌더.

검증된 GGUF 워크플로(UnetLoaderGGUF / CLIPLoaderGGUF + Wan22ImageToVideoLatent)를
API 프롬프트 형식으로 구성해 ComfyUI(/prompt, /history)에 제출/폴링한다.
"""
import os
import time
import requests

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://comfyui:8188")

# 검증된 모델 파일명 (workspace/models 안에 존재)
UNET_GGUF = os.environ.get("WAN_UNET_GGUF", "Wan2.2-TI2V-5B-Q5_K_M.gguf")
CLIP_GGUF = os.environ.get("WAN_CLIP_GGUF", "umt5-xxl-encoder-Q5_K_M.gguf")
VAE_NAME = os.environ.get("WAN_VAE", "wan2.2_vae.safetensors")

DEFAULT_NEG = ("blurry, low quality, distorted, static, overexposed, "
               "watermark, text, deformed, ugly")


def build_graph(positive, negative=None, width=704, height=480, length=49,
                steps=12, cfg=5.0, seed=0, fps=24, shift=8.0,
                start_image=None, filename_prefix="video/clip"):
    """WAN 2.2 5B GGUF API 그래프. start_image(파일명) 있으면 I2V."""
    neg = negative if negative is not None else DEFAULT_NEG
    g = {
        "37": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": UNET_GGUF}},
        "38": {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": CLIP_GGUF, "type": "wan"}},
        "39": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_NAME}},
        "48": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["37", 0], "shift": float(shift)}},
        "6":  {"class_type": "CLIPTextEncode", "inputs": {"clip": ["38", 0], "text": positive}},
        "7":  {"class_type": "CLIPTextEncode", "inputs": {"clip": ["38", 0], "text": neg}},
        "55": {"class_type": "Wan22ImageToVideoLatent",
               "inputs": {"vae": ["39", 0], "width": int(width), "height": int(height),
                          "length": int(length), "batch_size": 1}},
        "3":  {"class_type": "KSampler",
               "inputs": {"model": ["48", 0], "positive": ["6", 0], "negative": ["7", 0],
                          "latent_image": ["55", 0], "seed": int(seed), "steps": int(steps),
                          "cfg": float(cfg), "sampler_name": "uni_pc", "scheduler": "simple",
                          "denoise": 1.0}},
        "8":  {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
        "57": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": float(fps)}},
        "58": {"class_type": "SaveVideo",
               "inputs": {"video": ["57", 0], "filename_prefix": filename_prefix,
                          "format": "auto", "codec": "auto"}},
    }
    if start_image:
        g["56"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
        g["55"]["inputs"]["start_image"] = ["56", 0]
    return g


def submit(graph, client_id="yt-editor"):
    r = requests.post(f"{COMFYUI_URL}/prompt",
                      json={"prompt": graph, "client_id": client_id}, timeout=60)
    r.raise_for_status()
    return r.json()["prompt_id"]


def get_history(pid):
    for _ in range(15):  # ComfyUI 재시작 중 502 대비 재시도
        try:
            r = requests.get(f"{COMFYUI_URL}/history/{pid}", timeout=60)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(5)
    raise RuntimeError("ComfyUI history 조회 실패")


def wait(pid, on_status=None, timeout=2400):
    """완료까지 폴링. 성공 시 출력 파일명 리스트 반환."""
    start = time.time()
    last = None
    while True:
        time.sleep(5)
        h = get_history(pid)
        if pid in h:
            st = h[pid].get("status", {})
            s = st.get("status_str")
            if on_status and s != last:
                on_status(s)
                last = s
            if st.get("completed") or s in ("success", "error"):
                if s == "error":
                    msgs = st.get("messages", [])
                    raise RuntimeError("ComfyUI 실행 오류: " + str(msgs)[:500])
                files = []
                for o in h[pid].get("outputs", {}).values():
                    for k in ("videos", "gifs", "images"):
                        for f in o.get(k, []):
                            files.append(f.get("filename"))
                return files
        if time.time() - start > timeout:
            raise RuntimeError("ComfyUI 생성 타임아웃")


def ping():
    try:
        requests.get(f"{COMFYUI_URL}/system_stats", timeout=5).raise_for_status()
        return True
    except requests.RequestException:
        return False
