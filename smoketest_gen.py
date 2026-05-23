#!/usr/bin/env python3
# WAN 2.2 5B 스모크 테스트: 작은 T2V 생성을 제출하고 완료까지 폴링.
import json, time, urllib.request, uuid

HOST = "http://localhost:8188"
cid = uuid.uuid4().hex

prompt = {
    "37": {"class_type": "UNETLoader",
           "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
    "38": {"class_type": "CLIPLoader",
           "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}},
    "39": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
    "48": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["37", 0], "shift": 8.0}},
    "6":  {"class_type": "CLIPTextEncode",
           "inputs": {"clip": ["38", 0],
                      "text": "a cute orange cat walking in a sunny garden, gentle camera pan, cinematic"}},
    "7":  {"class_type": "CLIPTextEncode",
           "inputs": {"clip": ["38", 0],
                      "text": "blurry, low quality, distorted, static, watermark, text"}},
    "55": {"class_type": "Wan22ImageToVideoLatent",
           "inputs": {"vae": ["39", 0], "width": 512, "height": 384, "length": 25, "batch_size": 1}},
    "3":  {"class_type": "KSampler",
           "inputs": {"model": ["48", 0], "positive": ["6", 0], "negative": ["7", 0],
                      "latent_image": ["55", 0], "seed": 12345, "steps": 6, "cfg": 5.0,
                      "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}},
    "8":  {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
    "57": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": 16.0}},
    "58": {"class_type": "SaveVideo",
           "inputs": {"video": ["57", 0], "filename_prefix": "video/smoketest",
                      "format": "auto", "codec": "auto"}},
}

def post(path, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(HOST + path, data=data, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))

def get(path):
    return json.load(urllib.request.urlopen(HOST + path, timeout=60))

print("제출 중...", flush=True)
try:
    r = post("/prompt", {"prompt": prompt, "client_id": cid})
except urllib.error.HTTPError as e:
    print("PROMPT_ERROR:", e.read().decode()[:2000]); raise SystemExit(1)
pid = r["prompt_id"]
print("prompt_id:", pid, flush=True)

start = time.time()
while True:
    time.sleep(4)
    h = get("/history/" + pid)
    if pid in h:
        st = h[pid].get("status", {})
        print("상태:", st.get("status_str"), "| 경과 %.0fs" % (time.time()-start), flush=True)
        if st.get("completed") or st.get("status_str") in ("success", "error"):
            # 출력 파일 찾기
            outs = h[pid].get("outputs", {})
            files = []
            for nid, o in outs.items():
                for key in ("images", "gifs", "videos"):
                    for f in o.get(key, []):
                        files.append(f.get("subfolder", "") + "/" + f.get("filename", ""))
            print("RESULT_STATUS:", st.get("status_str"))
            print("OUTPUT_FILES:", files)
            if st.get("status_str") == "error":
                for m in st.get("messages", []):
                    print("MSG:", json.dumps(m, ensure_ascii=False)[:1500])
            break
    if time.time() - start > 1500:
        print("TIMEOUT (25분 초과)"); break
print("DONE", flush=True)
