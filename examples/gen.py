#!/usr/bin/env python3
# WAN 2.2 5B GGUF 본 생성 (사용자 프롬프트). 502 재시도 + 진행 표시.
import json, time, urllib.request, urllib.error, uuid

HOST = "http://localhost:8188"
cid = uuid.uuid4().hex

POS = "a cup of coffee on a wooden table, steam rising gently, warm morning sunlight streaming through a window, cozy atmosphere, shallow depth of field, cinematic"
NEG = "blurry, low quality, distorted, static, overexposed, watermark, text, deformed"
W, H, LEN, STEPS, FPS = 704, 480, 121, 12, 24

prompt = {
    "37": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "Wan2.2-TI2V-5B-Q5_K_M.gguf"}},
    "38": {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": "umt5-xxl-encoder-Q5_K_M.gguf", "type": "wan"}},
    "39": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
    "48": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["37", 0], "shift": 8.0}},
    "6":  {"class_type": "CLIPTextEncode", "inputs": {"clip": ["38", 0], "text": POS}},
    "7":  {"class_type": "CLIPTextEncode", "inputs": {"clip": ["38", 0], "text": NEG}},
    "55": {"class_type": "Wan22ImageToVideoLatent",
           "inputs": {"vae": ["39", 0], "width": W, "height": H, "length": LEN, "batch_size": 1}},
    "3":  {"class_type": "KSampler",
           "inputs": {"model": ["48", 0], "positive": ["6", 0], "negative": ["7", 0],
                      "latent_image": ["55", 0], "seed": 777, "steps": STEPS, "cfg": 5.0,
                      "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}},
    "8":  {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
    "57": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": float(FPS)}},
    "58": {"class_type": "SaveVideo",
           "inputs": {"video": ["57", 0], "filename_prefix": "video/coffee_5s", "format": "auto", "codec": "auto"}},
}

def get(path, tries=15):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(HOST + path, timeout=60))
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            print("  (재시도 %d: %s)" % (i+1, e), flush=True); time.sleep(5)
    raise RuntimeError("get failed: " + path)

def post(path, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(HOST + path, data=data, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))

print("제출 중 (%dx%d, %d프레임, %d스텝)..." % (W, H, LEN, STEPS), flush=True)
try:
    r = post("/prompt", {"prompt": prompt, "client_id": cid})
except urllib.error.HTTPError as e:
    print("PROMPT_ERROR:", e.read().decode()[:3000]); raise SystemExit(1)
pid = r["prompt_id"]
print("prompt_id:", pid, flush=True)

start = time.time()
last = None
while True:
    time.sleep(6)
    h = get("/history/" + pid)
    if pid in h:
        st = h[pid].get("status", {})
        s = st.get("status_str")
        if s != last:
            print("상태:", s, "| 경과 %.0fs" % (time.time()-start), flush=True); last = s
        if st.get("completed") or s in ("success", "error"):
            outs = h[pid].get("outputs", {})
            files = []
            for nid, o in outs.items():
                for key in ("images", "gifs", "videos"):
                    for f in o.get(key, []):
                        files.append(f.get("subfolder", "") + "/" + f.get("filename", ""))
            print("RESULT_STATUS:", s)
            print("OUTPUT_FILES:", files)
            print("TOTAL_TIME: %.0fs" % (time.time()-start))
            if s == "error":
                for m in st.get("messages", []):
                    print("MSG:", json.dumps(m, ensure_ascii=False)[:2000])
            break
    if time.time() - start > 1500:
        print("TIMEOUT (25분 초과)"); break
print("DONE", flush=True)
