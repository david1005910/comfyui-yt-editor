"""유튜브 영상용 AI 편집기 백엔드 (FastAPI + ffmpeg).

WAN 2.2 5B(GGUF) ComfyUI 워크플로로 클립을 생성하고, 브라우저에서 편집해
유튜브용 mp4(가로 16:9 / 세로 9:16 Shorts / 정사각 1:1)로 내보낸다.

기능
- 클립 생성: 텍스트→영상 / 이미지→영상 (단일)
- 긴 영상 생성: 구간 체이닝(마지막 프레임→다음 구간 시작 이미지)으로 5초+ 클립
- 라이브러리: 생성/업로드 클립·오디오 관리(삭제 포함)
- 타임라인 편집: 순서, 트림(in/out), 클립별 볼륨·페이드, 전환효과(크로스페이드 등),
  자막 스타일(위치·크기·색·박스), 타이틀/인트로 카드
- 유튜브 마무리: 썸네일 생성(프레임+제목), BGM 페이드/믹스
- 프로젝트: 타임라인 저장/불러오기/삭제
"""
import os
import re
import glob
import json
import time
import uuid
import math
import shutil
import threading
import subprocess

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import comfy_client as cc

# ───────────────────────── 경로 ─────────────────────────
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/data")
VIDEO_DIR = os.path.join(MEDIA_ROOT, "output", "video")        # 생성/업로드 클립
EXPORT_DIR = os.path.join(MEDIA_ROOT, "output", "exports")     # 최종 내보내기
ASSETS_DIR = os.path.join(MEDIA_ROOT, "output", "assets")      # 업로드 오디오
THUMB_DIR = os.path.join(MEDIA_ROOT, "output", "thumbnails")   # 썸네일
PROJECT_DIR = os.path.join(MEDIA_ROOT, "output", "projects")   # 프로젝트 JSON
INPUT_DIR = os.path.join(MEDIA_ROOT, "input")                  # ComfyUI input (I2V 시작 이미지)
for d in (VIDEO_DIR, EXPORT_DIR, ASSETS_DIR, THUMB_DIR, PROJECT_DIR, INPUT_DIR):
    os.makedirs(d, exist_ok=True)

CATEGORIES = {"video": VIDEO_DIR, "exports": EXPORT_DIR, "assets": ASSETS_DIR,
              "thumbnails": THUMB_DIR, "input": INPUT_DIR}

app = FastAPI(title="YT AI Editor")

# ───────────────────────── 작업(job) 상태 ─────────────────────────
JOBS = {}
JOBS_LOCK = threading.Lock()


def new_job(jtype):
    jid = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[jid] = {"id": jid, "type": jtype, "status": "queued",
                     "message": "대기 중", "progress": 0, "result": None,
                     "error": None, "created": time.time()}
    return jid


def set_job(jid, **kw):
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid].update(kw)


# ───────────────────────── 유틸 ─────────────────────────
def slug(s, n=40):
    """ASCII 전용 슬러그 (ComfyUI 출력 파일 접두사 등 안전한 경로용)."""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "clip").strip())[:n]
    return s or "clip"


def safe_name(s, n=60):
    """파일명/프로젝트명용. 한글 등 유니코드는 보존하고 경로·위험 문자만 제거."""
    s = (s or "").strip()
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", s).replace("..", "")
    s = re.sub(r"\s+", "_", s).strip("._")
    return s[:n] or "untitled"


def norm_color(c):
    """'#ffcc00' → '0xffcc00' (ffmpeg 색상 표기). 색 이름은 그대로."""
    c = (c or "").strip()
    if c.startswith("#"):
        return "0x" + c[1:]
    return c or "white"


def run_ff(cmd, timeout=1800):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 실패: " + (r.stderr or "")[-600:])
    return r


def ffprobe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return round(float(out), 3)
    except Exception:
        return 0.0


def has_audio(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return bool(out)
    except Exception:
        return False


def find_cjk_font():
    for pat in ("/usr/share/fonts/opentype/noto/NotoSansCJK*.ttc",
                "/usr/share/fonts/opentype/noto/NotoSerifCJK*.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK*.ttc",
                "/usr/share/fonts/truetype/nanum/NanumGothic*.ttf",
                "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf"):
        m = glob.glob(pat)
        if m:
            return m[0]
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


FONT = find_cjk_font()

# ── 텍스트 렌더링: Pillow 합성 (한글=Noto CJK, 이모지=Noto Color Emoji 컬러) ──
# drawtext/libass는 컬러 이모지(CBDT)를 못 그림 → Pillow로 RGBA PNG 합성 후 ffmpeg overlay.
try:
    from PIL import Image, ImageDraw, ImageFont
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False

_TMP_OVL = []  # 오버레이 PNG 임시파일 정리용

_NAME_RGB = {"white": (255, 255, 255), "black": (0, 0, 0), "yellow": (255, 210, 74),
             "red": (255, 45, 45), "gray": (176, 184, 196)}


def _rgb(c):
    c = (c or "").strip().lower()
    if c in _NAME_RGB:
        return _NAME_RGB[c]
    h = c.replace("#", "").replace("0x", "")
    if len(h) >= 6:
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError:
            pass
    return (255, 255, 255)


def _find_text_font():
    import glob as _g
    for pat in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
                "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf"):
        m = _g.glob(pat)
        if m:
            return m[0]
    return FONT


TEXT_FONT_PATH = _find_text_font()
EMOJI_FONT_PATH = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"


def _emoji_base_font():
    if not (_HAVE_PIL and os.path.exists(EMOJI_FONT_PATH)):
        return None
    for sz in (109, 136, 128, 96):
        try:
            return ImageFont.truetype(EMOJI_FONT_PATH, sz)
        except Exception:
            continue
    return None


_EMOJI_BASE = _emoji_base_font()


def _is_emoji(ch):
    o = ord(ch)
    return (0x1F300 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF
            or 0x1F1E6 <= o <= 0x1F1FF or 0x2300 <= o <= 0x23FF
            or o in (0x2122, 0x2139, 0x203C, 0x2049))


def _segments(text):
    """문자열을 (is_emoji, substring) 런으로 분할 (ZWJ/VS16 결합 처리)."""
    runs, cur, ce = [], "", None
    for ch in text:
        e = _is_emoji(ch) or ch in ("‍", "️")
        if ce is None:
            ce, cur = e, ch
        elif e == ce:
            cur += ch
        else:
            runs.append((ce, cur)); cur, ce = ch, e
    if cur:
        runs.append((ce, cur))
    return runs


def _draw_line(base, draw, text, cx, cy, fs, color, box=False, boxcolor="black", box_alpha=150):
    """한 줄을 (cx,cy) 중심으로 그림. 이모지는 컬러로 스케일 합성."""
    tf = ImageFont.truetype(TEXT_FONT_PATH, fs)
    target_h = int(fs * 1.15)
    parts, total = [], 0
    for is_e, s in _segments(text):
        if is_e and _EMOJI_BASE is not None:
            w0 = _EMOJI_BASE.getlength(s)
            if w0 <= 0:
                continue
            strike = _EMOJI_BASE.size
            ei = Image.new("RGBA", (int(w0) + 4, strike + 8), (0, 0, 0, 0))
            ImageDraw.Draw(ei).text((0, 0), s, font=_EMOJI_BASE, embedded_color=True)
            bb = ei.getbbox()
            if bb:
                ei = ei.crop(bb)
            scale = target_h / ei.height
            ei = ei.resize((max(1, int(ei.width * scale)), target_h), Image.LANCZOS)
            parts.append(("e", ei, ei.width)); total += ei.width
        else:
            w = draw.textlength(s, font=tf)
            parts.append(("t", s, w)); total += w
    if total <= 0:
        return
    if box:
        padx, pady = int(fs * 0.35), int(fs * 0.28)
        r, g, b = _rgb(boxcolor)
        draw.rounded_rectangle(
            [cx - total / 2 - padx, cy - target_h / 2 - pady,
             cx + total / 2 + padx, cy + target_h / 2 + pady],
            radius=int(fs * 0.18), fill=(r, g, b, box_alpha))
    x = cx - total / 2
    rgb = _rgb(color)
    for kind, obj, w in parts:
        if kind == "e":
            base.alpha_composite(obj, (int(x), int(cy - obj.height / 2)))
        else:
            draw.text((x, cy), obj, font=tf, fill=(rgb[0], rgb[1], rgb[2], 255), anchor="lm")
        x += w


def render_overlay(W, H, lines):
    """텍스트 줄 목록 → 투명 RGBA PNG(W×H) 작성, 경로 반환. lines:
    [{text, x, y, fs, color, box?, boxcolor?, box_alpha?}]"""
    lines = [L for L in lines if (L.get("text") or "").strip()]
    if not (_HAVE_PIL and lines):
        return None
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)
    for L in lines:
        _draw_line(base, draw, L["text"], L["x"], L["y"], int(L["fs"]), L["color"],
                   box=L.get("box", False), boxcolor=L.get("boxcolor", "black"),
                   box_alpha=L.get("box_alpha", 150))
    path = f"/tmp/ovl_{uuid.uuid4().hex[:10]}.png"
    base.save(path)
    _TMP_OVL.append(path)
    return path


def caption_overlay(text, W, H, pos="bottom", size=1.0, color="white", box=True):
    """자막 1줄 오버레이 PNG 경로 반환(없으면 None)."""
    fs = max(14, int(H * 0.060 * float(size)))
    y = {"top": int(H * 0.10), "center": H // 2,
         "bottom": H - int(H * 0.11)}.get(pos, H - int(H * 0.11))
    return render_overlay(W, H, [{"text": text, "x": W // 2, "y": y, "fs": fs,
                                  "color": color, "box": box, "boxcolor": "black", "box_alpha": 150}])


def _vf_overlay(chain, overlay_png, post=None):
    """비디오 필터 체인(chain) 위에 overlay_png(있으면)를 movie로 합성, 이후 post 필터 적용.
    -vf 단일 문자열로 사용 가능(filtergraph)."""
    s = ",".join(chain)
    if overlay_png:
        s = f"{s}[bg];movie={overlay_png}[ov];[bg][ov]overlay=0:0"
    if post:
        s = s + "," + ",".join(post)
    return s


# ═══════════════════════ 단일 클립 생성 ═══════════════════════
class GenReq(BaseModel):
    prompt: str
    negative: str | None = None
    width: int = 704
    height: int = 480
    length: int = 49
    steps: int = 12
    seed: int = 0
    start_image: str | None = None  # I2V: input 폴더의 파일명


def _norm_len(n):
    """WAN 길이는 4n+1 이어야 함."""
    n = max(5, int(n))
    return ((n - 1) // 4) * 4 + 1


def _run_generate(jid, req: GenReq):
    try:
        set_job(jid, status="running", message="ComfyUI에 제출 중...", progress=5)
        name = f"{slug(req.prompt)}_{int(time.time())}"
        seed = req.seed or (int(time.time()) % 2_000_000_000)
        graph = cc.build_graph(
            positive=req.prompt, negative=req.negative,
            width=req.width, height=req.height, length=_norm_len(req.length),
            steps=req.steps, seed=seed, start_image=req.start_image,
            filename_prefix=f"video/{name}")
        pid = cc.submit(graph)
        set_job(jid, message="생성 중 (모델 로딩→샘플링→디코드)...", progress=30, comfy_pid=pid)
        files = cc.wait(pid, on_status=lambda s: set_job(jid, message=f"ComfyUI 상태: {s}"))
        if not files:
            raise RuntimeError("출력 파일 없음")
        out = os.path.basename(files[0])
        set_job(jid, status="done", message="완료", progress=100,
                result={"file": out, "category": "video",
                        "duration": ffprobe_duration(os.path.join(VIDEO_DIR, out))})
    except Exception as e:
        set_job(jid, status="error", message="실패", error=str(e))


@app.post("/api/generate")
def generate(req: GenReq):
    if not req.prompt.strip():
        raise HTTPException(400, "프롬프트가 비어 있습니다")
    jid = new_job("generate")
    threading.Thread(target=_run_generate, args=(jid, req), daemon=True).start()
    return {"job_id": jid}


# ═══════════════════════ 긴 영상 생성 (체이닝) ═══════════════════════
class LongGenReq(BaseModel):
    prompt: str
    negative: str | None = None
    width: int = 704
    height: int = 480
    seconds: float = 10.0          # 목표 길이(초)
    seg_length: int = 121          # 구간당 프레임 (121f≈5초)
    steps: int = 12
    seed: int = 0
    fps: int = 24
    start_image: str | None = None


def _extract_last_frame(src_mp4, dst_png):
    """클립의 마지막 프레임을 input 폴더에 저장(다음 구간 시작 이미지)."""
    run_ff(["ffmpeg", "-y", "-sseof", "-0.15", "-i", src_mp4,
            "-update", "1", "-frames:v", "1", dst_png], timeout=120)


def _concat_dedup(seg_paths, out_path, fps, w, h):
    """구간 연결. 2번째 구간부터 첫 프레임(중복) 제거 + 해상도/sar/fps 정규화 후 concat."""
    inputs = []
    for p in seg_paths:
        inputs += ["-i", p]
    parts, fc = [], ""
    for i in range(len(seg_paths)):
        pre = "trim=start_frame=1,setpts=PTS-STARTPTS," if i > 0 else ""
        fc += (f"[{i}:v]{pre}scale={w}:{h}:force_original_aspect_ratio=decrease,"
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[s{i}];")
        parts.append(f"[s{i}]")
    fc += "".join(parts) + f"concat=n={len(seg_paths)}:v=1[out]"
    run_ff(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[out]",
            "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", out_path], timeout=1800)


def _run_generate_long(jid, req: LongGenReq):
    seg_files, tmp = [], []
    try:
        L = _norm_len(req.seg_length)
        sec_per_seg = L / float(req.fps)
        n = max(1, math.ceil(req.seconds / sec_per_seg))
        base = f"{slug(req.prompt, 30)}_{int(time.time())}"
        seed = req.seed or (int(time.time()) % 2_000_000_000)
        set_job(jid, status="running", progress=2,
                message=f"긴 영상 생성: 총 {n}구간 (~{round(n*sec_per_seg,1)}초)")

        prev = req.start_image
        for k in range(1, n + 1):
            set_job(jid, message=f"구간 {k}/{n} 생성 중 (모델 로딩→샘플링→디코드)...",
                    progress=int(5 + 80 * (k - 1) / n))
            graph = cc.build_graph(
                positive=req.prompt, negative=req.negative,
                width=req.width, height=req.height, length=L, steps=req.steps,
                seed=seed, fps=req.fps, start_image=prev,
                filename_prefix=f"video/{base}_seg{k}")
            pid = cc.submit(graph)
            files = cc.wait(pid, on_status=lambda s, k=k: set_job(
                jid, message=f"구간 {k}/{n} · ComfyUI 상태: {s}"))
            if not files:
                raise RuntimeError(f"구간 {k} 출력 없음")
            seg = os.path.join(VIDEO_DIR, os.path.basename(files[0]))
            seg_files.append(seg)
            if k < n:
                prev = f"{base}_lf{k}.png"
                _extract_last_frame(seg, os.path.join(INPUT_DIR, prev))
                tmp.append(os.path.join(INPUT_DIR, prev))

        if len(seg_files) == 1:
            out_name = f"{base}.mp4"
            shutil.move(seg_files[0], os.path.join(VIDEO_DIR, out_name))
        else:
            set_job(jid, message=f"{len(seg_files)}개 구간 연결 중...", progress=88)
            out_name = f"{base}.mp4"
            _concat_dedup(seg_files, os.path.join(VIDEO_DIR, out_name),
                          req.fps, req.width, req.height)
            for s in seg_files:  # 중간 구간 파일 정리(최종본만 남김)
                try:
                    os.remove(s)
                except OSError:
                    pass

        out_path = os.path.join(VIDEO_DIR, out_name)
        set_job(jid, status="done", message="완료", progress=100,
                result={"file": out_name, "category": "video",
                        "duration": ffprobe_duration(out_path)})
    except Exception as e:
        set_job(jid, status="error", message="긴 영상 생성 실패", error=str(e))
    finally:
        for t in tmp:
            try:
                os.remove(t)
            except OSError:
                pass


@app.post("/api/generate_long")
def generate_long(req: LongGenReq):
    if not req.prompt.strip():
        raise HTTPException(400, "프롬프트가 비어 있습니다")
    jid = new_job("generate_long")
    threading.Thread(target=_run_generate_long, args=(jid, req), daemon=True).start()
    return {"job_id": jid}


# ═══════════════════════ 업로드 / 목록 / 삭제 ═══════════════════════
@app.post("/api/upload")
async def upload(file: UploadFile = File(...), kind: str = Form("video")):
    fn = re.sub(r"[^a-zA-Z0-9._-]+", "_", os.path.basename(file.filename or "upload"))
    dest_dir = {"video": VIDEO_DIR, "image": INPUT_DIR, "audio": ASSETS_DIR}.get(kind, ASSETS_DIR)
    path = os.path.join(dest_dir, fn)
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    cat = {"video": "video", "image": "input", "audio": "assets"}.get(kind, "assets")
    return {"filename": fn, "category": cat,
            "duration": ffprobe_duration(path) if kind != "image" else None}


@app.get("/api/clips")
def clips():
    items = []
    for p in sorted(glob.glob(os.path.join(VIDEO_DIR, "*.mp4")),
                    key=os.path.getmtime, reverse=True):
        items.append({"file": os.path.basename(p), "category": "video",
                      "duration": ffprobe_duration(p), "size": os.path.getsize(p)})
    return {"clips": items}


@app.delete("/api/clips/{filename}")
def delete_clip(filename: str):
    p = os.path.join(VIDEO_DIR, os.path.basename(filename))
    if not os.path.exists(p):
        raise HTTPException(404, "not found")
    os.remove(p)
    return {"ok": True}


@app.get("/api/audio")
def audio_list():
    items = []
    for ext in ("*.mp3", "*.wav", "*.m4a", "*.aac", "*.ogg", "*.flac"):
        for p in glob.glob(os.path.join(ASSETS_DIR, ext)):
            items.append({"file": os.path.basename(p), "category": "assets",
                          "duration": ffprobe_duration(p)})
    return {"audio": items}


@app.delete("/api/audio/{filename}")
def delete_audio(filename: str):
    p = os.path.join(ASSETS_DIR, os.path.basename(filename))
    if not os.path.exists(p):
        raise HTTPException(404, "not found")
    os.remove(p)
    return {"ok": True}


@app.get("/api/exports")
def exports_list():
    items = []
    for p in sorted(glob.glob(os.path.join(EXPORT_DIR, "*.mp4")),
                    key=os.path.getmtime, reverse=True):
        items.append({"file": os.path.basename(p), "category": "exports",
                      "duration": ffprobe_duration(p), "size": os.path.getsize(p)})
    return {"exports": items}


@app.get("/api/thumbnails")
def thumbs_list():
    items = []
    for ext in ("*.jpg", "*.png"):
        for p in sorted(glob.glob(os.path.join(THUMB_DIR, ext)),
                        key=os.path.getmtime, reverse=True):
            items.append({"file": os.path.basename(p), "category": "thumbnails",
                          "size": os.path.getsize(p)})
    return {"thumbnails": items}


@app.get("/api/jobs/{jid}")
def job_status(jid):
    with JOBS_LOCK:
        j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


# ═══════════════════════ 썸네일 생성 ═══════════════════════
class ThumbReq(BaseModel):
    file: str                    # video 폴더 클립
    time: float = 1.0            # 추출 시점(초)
    title: str | None = None
    subtitle: str | None = None
    title_color: str = "#ffffff"
    bg: str | None = None        # 제목 박스 색(없으면 반투명 검정)
    width: int = 1280
    height: int = 720


@app.post("/api/thumbnail")
def thumbnail(req: ThumbReq):
    src = os.path.join(VIDEO_DIR, os.path.basename(req.file))
    if not os.path.exists(src):
        raise HTTPException(404, f"클립 없음: {req.file}")
    W, H = req.width, req.height
    out_name = f"thumb_{safe_name(req.title or req.file, 30)}_{int(time.time())}.jpg"
    out = os.path.join(THUMB_DIR, out_name)
    base = [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}",
            "eq=contrast=1.05:saturation=1.15"]  # 살짝 강조
    tlines = []
    if req.title:
        tlines.append({"text": req.title, "x": W // 2, "y": int(H * 0.66),
                       "fs": int(H * 0.13), "color": req.title_color,
                       "box": True, "boxcolor": req.bg or "black", "box_alpha": 170})
    if req.subtitle:
        tlines.append({"text": req.subtitle, "x": W // 2, "y": int(H * 0.88),
                       "fs": int(H * 0.06), "color": "yellow",
                       "box": True, "boxcolor": "black", "box_alpha": 150})
    ovl = render_overlay(W, H, tlines)
    try:
        run_ff(["ffmpeg", "-y", "-ss", str(max(0, req.time)), "-i", src,
                "-frames:v", "1", "-vf", _vf_overlay(base, ovl), "-q:v", "2", out], timeout=120)
    finally:
        _cleanup_texts()
    return {"file": out_name, "category": "thumbnails"}


# ═══════════════════════ 내보내기 ═══════════════════════
class Clip(BaseModel):
    type: str = "video"            # "video" | "card"
    file: str | None = None
    start: float = 0.0
    end: float | None = None
    # 카드 전용
    card_text: str | None = None
    card_subtext: str | None = None
    card_bg: str = "#101418"
    card_duration: float = 3.0
    # 자막
    text: str | None = None
    caption_pos: str = "bottom"    # top|center|bottom
    caption_size: float = 1.0
    caption_color: str = "#ffffff"
    caption_box: bool = True
    # 오디오/페이드/전환
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    transition: str = "none"       # 이전 클립→이 클립 전환: none|fade|wipeleft|wiperight|slideleft|circleopen|dissolve
    transition_dur: float = 0.5


class ExportReq(BaseModel):
    name: str = "youtube_export"
    clips: list[Clip]
    music: str | None = None
    music_volume: float = 0.6
    music_fade: float = 1.5
    keep_clip_audio: bool = True   # 클립 원본 오디오 유지 여부
    width: int = 1920
    height: int = 1080
    fps: int = 24


AR_PRESETS = {  # 프론트 참고용
    "16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080),
}


def _cleanup_texts():
    while _TMP_OVL:
        t = _TMP_OVL.pop()
        try:
            os.remove(t)
        except OSError:
            pass


def _build_segment(c: Clip, W, H, FPS, seg_path, keep_clip_audio):
    """클립/카드 → 정규화 세그먼트(영상+오디오, 동일 코덱). 반환: 실제 길이(초)."""
    vbase = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    abase = ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k"]

    if c.type == "card":
        dur = max(0.3, float(c.card_duration))
        cardlines = []
        if c.card_text:
            cardlines.append({"text": c.card_text, "x": W // 2, "y": int(H * 0.46),
                              "fs": int(H * 0.10), "color": "white"})
        if c.card_subtext:
            cardlines.append({"text": c.card_subtext, "x": W // 2, "y": int(H * 0.59),
                              "fs": int(H * 0.05), "color": "#b0b8c4"})
        ovl = render_overlay(W, H, cardlines)
        post = []
        if c.fade_in > 0:
            post.append(f"fade=t=in:st=0:d={c.fade_in}")
        if c.fade_out > 0:
            post.append(f"fade=t=out:st={max(0, dur-c.fade_out):.3f}:d={c.fade_out}")
        vf = _vf_overlay(["format=yuv420p"], ovl, post)
        run_ff(["ffmpeg", "-y",
                "-f", "lavfi", "-i", f"color=c={norm_color(c.card_bg)}:s={W}x{H}:r={FPS}:d={dur}",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", f"{dur}", "-map", "0:v", "-map", "1:a",
                "-vf", vf, *vbase, *abase, "-shortest", seg_path], timeout=600)
        return dur

    # ── 비디오 클립 ──
    src = os.path.join(VIDEO_DIR, os.path.basename(c.file or ""))
    if not os.path.exists(src):
        raise RuntimeError(f"클립 없음: {c.file}")
    src_dur = ffprobe_duration(src)
    start = max(0.0, float(c.start or 0))
    end = c.end if (c.end and c.end > start) else src_dur
    end = min(end, src_dur)
    dur = round(max(0.1, end - start), 3)

    base = [f"scale={W}:{H}:force_original_aspect_ratio=decrease",
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black", f"fps={FPS}", "format=yuv420p"]
    post = []
    if c.fade_in > 0:
        post.append(f"fade=t=in:st=0:d={c.fade_in}")
    if c.fade_out > 0:
        post.append(f"fade=t=out:st={max(0, dur-c.fade_out):.3f}:d={c.fade_out}")
    ovl = None
    if c.text and c.text.strip():
        ovl = caption_overlay(c.text.strip(), W, H, c.caption_pos, c.caption_size,
                              c.caption_color, c.caption_box)
    vf = _vf_overlay(base, ovl, post)

    src_has_audio = keep_clip_audio and has_audio(src)
    cmd = ["ffmpeg", "-y", "-ss", f"{start}", "-i", src]
    if not src_has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-t", f"{dur}"]
    if not src_has_audio:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-vf", vf]
    if src_has_audio:
        af = [f"volume={max(0.0, c.volume)}"]
        if c.fade_in > 0:
            af.append(f"afade=t=in:st=0:d={c.fade_in}")
        if c.fade_out > 0:
            af.append(f"afade=t=out:st={max(0, dur-c.fade_out):.3f}:d={c.fade_out}")
        cmd += ["-af", ",".join(af)]
    cmd += [*vbase, *abase, "-shortest", seg_path]
    run_ff(cmd, timeout=1200)
    return ffprobe_duration(seg_path)


def _run_export(jid, req: ExportReq):
    work = f"/tmp/exp_{jid}"
    os.makedirs(work, exist_ok=True)
    tmp = [work]
    try:
        if not req.clips:
            raise RuntimeError("타임라인이 비어 있습니다")
        W, H, FPS = req.width, req.height, req.fps
        n = len(req.clips)
        segs, durs = [], []
        for i, c in enumerate(req.clips):
            set_job(jid, status="running", message=f"클립 {i+1}/{n} 처리 중...",
                    progress=int(5 + 60 * i / n))
            seg = os.path.join(work, f"seg{i:03d}.mp4")
            d = _build_segment(c, W, H, FPS, seg, req.keep_clip_audio)
            segs.append(seg)
            durs.append(d)

        use_xfade = any(
            c.transition and c.transition != "none" for c in req.clips[1:])
        concat = os.path.join(work, "concat.mp4")

        if not use_xfade:
            set_job(jid, message="클립 연결 중...", progress=70)
            listf = os.path.join(work, "list.txt")
            with open(listf, "w") as f:
                for s in segs:
                    f.write(f"file '{s}'\n")
            run_ff(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                    "-c", "copy", concat], timeout=600)
        else:
            set_job(jid, message="전환효과 합성 중...", progress=70)
            inputs = []
            for s in segs:
                inputs += ["-i", s]
            vlab, alab, fc, cum = "[0:v]", "[0:a]", "", durs[0]
            for i in range(1, n):
                c = req.clips[i]
                if c.transition and c.transition != "none":
                    d = max(0.05, min(float(c.transition_dur), durs[i] - 0.05, durs[i-1] - 0.05))
                    trans = c.transition
                else:
                    d, trans = 1.0 / FPS, "fade"   # 하드컷(1프레임)
                off = max(0.0, cum - d)
                fc += f"{vlab}[{i}:v]xfade=transition={trans}:duration={d:.4f}:offset={off:.4f}[v{i}];"
                fc += f"{alab}[{i}:a]acrossfade=d={d:.4f}[a{i}];"
                vlab, alab = f"[v{i}]", f"[a{i}]"
                cum = cum + durs[i] - d
            fc = fc.rstrip(";")
            run_ff(["ffmpeg", "-y", *inputs, "-filter_complex", fc,
                    "-map", vlab, "-map", alab,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", concat],
                   timeout=1800)

        total = ffprobe_duration(concat)
        out_name = f"{safe_name(req.name)}_{int(time.time())}.mp4"
        out_path = os.path.join(EXPORT_DIR, out_name)

        if req.music:
            set_job(jid, message="배경음악 믹스 중...", progress=88)
            music_path = os.path.join(ASSETS_DIR, os.path.basename(req.music))
            if not os.path.exists(music_path):
                raise RuntimeError(f"음악 파일 없음: {req.music}")
            mv = max(0.0, min(2.0, req.music_volume))
            mf = max(0.0, float(req.music_fade))
            mfilt = [f"volume={mv}"]
            if mf > 0:
                mfilt.append(f"afade=t=in:st=0:d={mf}")
                mfilt.append(f"afade=t=out:st={max(0, total-mf):.3f}:d={mf}")
            fc = (f"[1:a]{','.join(mfilt)}[m];"
                  f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[a]")
            run_ff(["ffmpeg", "-y", "-i", concat, "-stream_loop", "-1", "-i", music_path,
                    "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", out_path], timeout=900)
        else:
            set_job(jid, message="마무리 중...", progress=92)
            try:
                run_ff(["ffmpeg", "-y", "-i", concat, "-c", "copy",
                        "-movflags", "+faststart", out_path], timeout=300)
            except Exception:
                shutil.copy(concat, out_path)

        set_job(jid, status="done", message="내보내기 완료", progress=100,
                result={"file": out_name, "category": "exports",
                        "duration": ffprobe_duration(out_path),
                        "size": os.path.getsize(out_path)})
    except Exception as e:
        set_job(jid, status="error", message="내보내기 실패", error=str(e))
    finally:
        _cleanup_texts()
        shutil.rmtree(work, ignore_errors=True)


@app.post("/api/export")
def export(req: ExportReq):
    jid = new_job("export")
    threading.Thread(target=_run_export, args=(jid, req), daemon=True).start()
    return {"job_id": jid}


# ═══════════════════════ 프로젝트 저장/불러오기 ═══════════════════════
class ProjectReq(BaseModel):
    name: str
    data: dict


@app.get("/api/projects")
def projects_list():
    items = []
    for p in sorted(glob.glob(os.path.join(PROJECT_DIR, "*.json")),
                    key=os.path.getmtime, reverse=True):
        items.append({"name": os.path.splitext(os.path.basename(p))[0],
                      "updated": round(os.path.getmtime(p))})
    return {"projects": items}


@app.post("/api/projects")
def project_save(req: ProjectReq):
    name = safe_name(req.name, 60)
    with open(os.path.join(PROJECT_DIR, f"{name}.json"), "w") as f:
        json.dump({"name": name, "data": req.data, "saved": time.time()},
                  f, ensure_ascii=False, indent=2)
    return {"name": name, "ok": True}


@app.get("/api/projects/{name}")
def project_load(name: str):
    p = os.path.join(PROJECT_DIR, f"{safe_name(name, 60)}.json")
    if not os.path.exists(p):
        raise HTTPException(404, "프로젝트 없음")
    with open(p) as f:
        return json.load(f)


@app.delete("/api/projects/{name}")
def project_delete(name: str):
    p = os.path.join(PROJECT_DIR, f"{safe_name(name, 60)}.json")
    if not os.path.exists(p):
        raise HTTPException(404, "프로젝트 없음")
    os.remove(p)
    return {"ok": True}


# ═══════════════════════ 미디어 서빙 ═══════════════════════
@app.get("/api/media/{category}/{filename}")
def media(category: str, filename: str):
    d = CATEGORIES.get(category)
    if not d:
        raise HTTPException(404, "bad category")
    path = os.path.join(d, os.path.basename(filename))
    if not os.path.exists(path):
        raise HTTPException(404, "not found")
    return FileResponse(path)


@app.get("/api/download/{category}/{filename}")
def download(category: str, filename: str):
    d = CATEGORIES.get(category)
    if not d:
        raise HTTPException(404, "bad category")
    path = os.path.join(d, os.path.basename(filename))
    if not os.path.exists(path):
        raise HTTPException(404, "not found")
    return FileResponse(path, filename=os.path.basename(filename),
                        media_type="application/octet-stream")


@app.get("/api/status")
def status():
    return {"comfyui": cc.ping(), "font": FONT, "aspect_presets": AR_PRESETS,
            "models": {"unet": cc.UNET_GGUF, "clip": cc.CLIP_GGUF, "vae": cc.VAE_NAME}}


# 정적 프론트엔드
app.mount("/", StaticFiles(directory="static", html=True), name="static")
