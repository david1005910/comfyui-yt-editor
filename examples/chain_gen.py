#!/usr/bin/env python3
# WAN 2.2 5B GGUF chaining: N개 구간을 이어붙여 5초보다 긴 영상 생성.
#   구간1 = 텍스트->영상, 구간k = (구간 k-1의 마지막 프레임)을 시작 이미지로 한 영상.
#   마지막에 ffmpeg(docker exec)로 연결(중복 첫 프레임 제거).
import json, time, urllib.request, urllib.error, uuid, subprocess

HOST = "http://localhost:8188"
cid = uuid.uuid4().hex

# ===== 설정 =====
POS = "a cup of coffee on a wooden table, steam rising gently, warm morning sunlight streaming through a window, cozy atmosphere, shallow depth of field, cinematic"
NEG = "blurry, low quality, distorted, static, overexposed, watermark, text, deformed"
W, H, LEN, STEPS, FPS = 704, 480, 121, 12, 24
SEGMENTS = 2          # 2구간 = 약 10초 (구간당 5초)
SEED = 777
NAME = "coffee_10s"
# 컨테이너 내부 경로
C_OUT = "/opt/ComfyUI/output/video"
C_IN  = "/opt/ComfyUI/input"

def dexec(args):
    return subprocess.run(["docker","exec","comfyui","sh","-c",args],
                          capture_output=True, text=True)

def get(path, tries=20):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(HOST + path, timeout=60))
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            print("   (재시도 %d: %s)" % (i+1, e), flush=True); time.sleep(5)
    raise RuntimeError("get failed: " + path)

def submit_and_wait(graph, label):
    data = json.dumps({"prompt": graph, "client_id": cid}).encode()
    req = urllib.request.Request(HOST + "/prompt", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        pid = json.load(urllib.request.urlopen(req, timeout=60))["prompt_id"]
    except urllib.error.HTTPError as e:
        print("PROMPT_ERROR:", e.read().decode()[:3000]); raise
    print("[%s] 제출됨 pid=%s" % (label, pid), flush=True)
    start = time.time()
    while True:
        time.sleep(6)
        h = get("/history/" + pid)
        if pid in h:
            st = h[pid].get("status", {})
            s = st.get("status_str")
            if st.get("completed") or s in ("success","error"):
                if s == "error":
                    for m in st.get("messages", []):
                        print("MSG:", json.dumps(m, ensure_ascii=False)[:1500])
                    raise RuntimeError("%s 실행 오류" % label)
                files = []
                for o in h[pid].get("outputs", {}).values():
                    for k in ("videos","gifs","images"):
                        for f in o.get(k, []):
                            files.append(f.get("filename"))
                print("[%s] 완료 %.0fs -> %s" % (label, time.time()-start, files), flush=True)
                return files
        if time.time()-start > 1500:
            raise RuntimeError("%s 타임아웃" % label)

def base_graph(start_image=None, fileprefix="video/seg"):
    g = {
        "37": {"class_type":"UnetLoaderGGUF","inputs":{"unet_name":"Wan2.2-TI2V-5B-Q5_K_M.gguf"}},
        "38": {"class_type":"CLIPLoaderGGUF","inputs":{"clip_name":"umt5-xxl-encoder-Q5_K_M.gguf","type":"wan"}},
        "39": {"class_type":"VAELoader","inputs":{"vae_name":"wan2.2_vae.safetensors"}},
        "48": {"class_type":"ModelSamplingSD3","inputs":{"model":["37",0],"shift":8.0}},
        "6":  {"class_type":"CLIPTextEncode","inputs":{"clip":["38",0],"text":POS}},
        "7":  {"class_type":"CLIPTextEncode","inputs":{"clip":["38",0],"text":NEG}},
        "55": {"class_type":"Wan22ImageToVideoLatent",
               "inputs":{"vae":["39",0],"width":W,"height":H,"length":LEN,"batch_size":1}},
        "3":  {"class_type":"KSampler",
               "inputs":{"model":["48",0],"positive":["6",0],"negative":["7",0],
                         "latent_image":["55",0],"seed":SEED,"steps":STEPS,"cfg":5.0,
                         "sampler_name":"uni_pc","scheduler":"simple","denoise":1.0}},
        "8":  {"class_type":"VAEDecode","inputs":{"samples":["3",0],"vae":["39",0]}},
        "57": {"class_type":"CreateVideo","inputs":{"images":["8",0],"fps":float(FPS)}},
        "58": {"class_type":"SaveVideo",
               "inputs":{"video":["57",0],"filename_prefix":fileprefix,"format":"auto","codec":"auto"}},
    }
    if start_image:
        g["56"] = {"class_type":"LoadImage","inputs":{"image":start_image}}
        g["55"]["inputs"]["start_image"] = ["56",0]
    return g

seg_files = []
prev_last = None
for k in range(1, SEGMENTS+1):
    if k == 1:
        g = base_graph(start_image=None, fileprefix="video/%s_seg%d" % (NAME,k))
        files = submit_and_wait(g, "구간%d/T2V" % k)
    else:
        g = base_graph(start_image=prev_last, fileprefix="video/%s_seg%d" % (NAME,k))
        files = submit_and_wait(g, "구간%d/I2V" % k)
    seg_mp4 = files[0]
    seg_files.append(seg_mp4)
    # 다음 구간을 위해 이 구간의 마지막 프레임 추출 -> input 폴더
    if k < SEGMENTS:
        prev_last = "%s_lastframe_%d.png" % (NAME, k)
        r = dexec("ffmpeg -y -sseof -0.2 -i %s/%s -update 1 -frames:v 1 %s/%s 2>&1 | tail -1"
                  % (C_OUT, seg_mp4, C_IN, prev_last))
        print("   마지막 프레임 추출:", prev_last, "|", r.stdout.strip()[-80:], flush=True)

# 연결: 구간1 전체 + 이후 구간들은 첫 프레임(중복) 제거
print("[concat] %d개 구간 연결 중..." % len(seg_files), flush=True)
inputs = " ".join("-i %s/%s" % (C_OUT,f) for f in seg_files)
parts = ["[0:v]"]
fc = ""
for i in range(1, len(seg_files)):
    fc += "[%d:v]trim=start_frame=1,setpts=PTS-STARTPTS[s%d];" % (i, i)
    parts.append("[s%d]" % i)
fc += "%sconcat=n=%d:v=1[out]" % ("".join(parts), len(seg_files))
cmd = "ffmpeg -y %s -filter_complex \"%s\" -map \"[out]\" -r %d -pix_fmt yuv420p %s/%s.mp4 2>&1 | tail -2" \
      % (inputs, fc, FPS, C_OUT, NAME)
r = dexec(cmd)
print(r.stdout.strip()[-300:], flush=True)
print("FINAL_FILE: video/%s.mp4" % NAME, flush=True)
print("DONE", flush=True)
