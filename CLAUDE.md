# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A two-service Docker Compose app that **generates AI video clips locally** (WAN 2.2 5B via ComfyUI on an NVIDIA GPU) and provides a browser-based **video editor** that exports YouTube-ready mp4 (16:9 / 9:16 Shorts / 1:1). Runs on Windows 11 + WSL2 + Docker Desktop with GPU passthrough; the target GPU is an RTX 3060 12GB eGPU.

User-facing docs (`README.md`, `START_HERE.md`) and all code comments are in Korean.

## Commands

```bash
bash run.sh                       # one-click: checks Docker/GPU, downloads models, builds & starts
docker compose up -d --build      # build & start (REQUIRED after editing any code — code is baked into images)
docker compose logs -f editor     # or: ... comfyui
docker compose stop / down        # stop / remove containers (workspace/ models persist either way)

bash scripts/download_models.sh        # WAN 2.2 5B GGUF (~8GB, default)
bash scripts/download_models.sh fp16   # additionally fetch fp16 originals (needs RAM 16GB+)
sudo bash scripts/add_swap.sh          # 12GB swap to avoid OOM (exit 137) when RAM ≤ 16GB
```

Smoke tests run a tiny generation against a live ComfyUI at `localhost:8188` (start the stack first):
```bash
python3 smoketest_gguf.py     # GGUF path (matches the editor's real workflow)
python3 smoketest_gen.py      # fp16 path
python3 examples/gen.py       # full single-clip generation (CLI)
python3 examples/chain_gen.py # long-video chaining demo (edit constants at top)
```
There is no test framework, linter, or build step beyond Docker. The smoke tests **are** the integration check.

**Ports:** editor `http://localhost:8090`, ComfyUI `http://localhost:8188`, ai-dock portal `:1111`, Jupyter `:8888`.

## Architecture

Two containers on the compose network, communicating only over HTTP, sharing state only through bind-mounted volumes:

```
editor (:8090→8080)  --HTTP /prompt,/history-->  comfyui (:8188, GPU)
   FastAPI + ffmpeg                                  WAN 2.2 + ComfyUI-GGUF
        \________ shared volumes: workspace/output, workspace/input ________/
```

- **`comfyui`** — built from `Dockerfile` on `ghcr.io/ai-dock/comfyui:latest-cuda`. The base image's bundled ComfyUI (v0.2.2) lacks WAN 2.2 nodes, so the build **checks out latest ComfyUI master and installs ComfyUI-GGUF, baking them into the image** (survives container recreation, fast startup). `AUTO_UPDATE=false` and Cloudflare tunnels are disabled — this is a local-only, no-auth deployment.
- **`editor`** — `editor/Dockerfile` (python:3.11-slim + ffmpeg + Noto CJK/emoji fonts). `editor/app.py` is the FastAPI backend; `editor/static/` is a single-page tabbed UI (no framework, plain JS).

The editor reaches ComfyUI via `COMFYUI_URL=http://comfyui:8188`. The two containers exchange files through shared host directories: the editor reads generated clips from `workspace/output` (mounted as `/data/output`) and writes I2V start images to `workspace/input` (mounted as `/data/input`, which ComfyUI sees as its `input/`). **A path written by one container must be reachable by the other through these mounts** — this is why generated filenames, not absolute paths, are passed around.

### The WAN 2.2 generation graph

`editor/comfy_client.py::build_graph()` is the **canonical** ComfyUI API graph (numbered-node dict: `UnetLoaderGGUF` → `CLIPLoaderGGUF` → `ModelSamplingSD3` → `KSampler` → `VAEDecode` → `CreateVideo` → `SaveVideo`; I2V adds a `LoadImage` node feeding `Wan22ImageToVideoLatent.start_image`). The standalone CLI scripts (`examples/gen.py`, `examples/chain_gen.py`, `smoketest_*.py`) each **inline their own copy of this same graph** — if you change node wiring, model names, or sampler settings, update `comfy_client.py` plus any scripts you care about; they do not share code.

Model files are referenced by **exact filename** via constants in `comfy_client.py` (`UNET_GGUF`, `CLIP_GGUF`, `VAE_NAME`), overridable by env var. These must match what `scripts/download_models.sh` places under `workspace/models/{unet,text_encoders,vae}/`.

**WAN frame-count constraint:** clip length must be `4n+1` (e.g. 49/81/121; 121f ≈ 5s @ 24fps, the single-clip max). `app.py::_norm_len()` enforces this — preserve it anywhere you compute lengths.

### Async job model

Generation and export are long-running, so `app.py` runs them on background `threading.Thread`s and tracks them in an in-memory `JOBS` dict (guarded by `JOBS_LOCK`). Endpoints return `{job_id}` immediately; the frontend polls `GET /api/jobs/{jid}` every 2.5s (`pollJob` in `app.js`) for `status`/`progress`/`message`/`result`/`error`. **Jobs are not persisted** — a container restart loses in-flight job state. `comfy_client.py` itself polls ComfyUI's `/history/{pid}` and retries on 502 (ComfyUI restarting mid-request).

### ffmpeg pipeline (`app.py`)

Export is the most intricate part:
1. **Per-clip normalization** (`_build_segment`) — each timeline item (video clip or title "card") is rendered to a uniform mp4 (scale+pad to target W×H, fps, yuv420p, AAC stereo), applying trim, per-clip volume, fades, and captions. Cards are generated from an `lavfi color` source + `anullsrc`.
2. **Stitching** — if no transitions are used, fast `concat` demuxer with `-c copy`; if any clip has a transition, an `xfade` + `acrossfade` `filter_complex` chain with computed offsets (hard cuts become a 1-frame `fade`).
3. **Music** — optional background track mixed in with `amix`, looped (`-stream_loop -1`) and faded.

**Long-video chaining** (`_run_generate_long`): generate segment → extract its last frame (`-sseof`) into `workspace/input` → use as next segment's I2V start image → `_concat_dedup` joins segments, dropping each later segment's duplicated first frame (`trim=start_frame=1`).

**Text rendering** (`render_overlay`, `_draw_line`): captions/titles are drawn with **Pillow into a transparent RGBA PNG, then composited via ffmpeg `movie`+`overlay`** — *not* `drawtext`/libass, because those cannot render color emoji (CBDT). The code splits strings into text vs. emoji runs and scales color-emoji glyphs to match the text height. Keep this approach if touching captions; it's the reason for the Pillow dependency and the Noto Color Emoji font in the Dockerfile.

The editor's ffmpeg features (edit/export/thumbnail) **work without a GPU or ComfyUI** — only clip *generation* needs them.

## Conventions & gotchas

- **`workspace/`** (created at runtime) holds models, inputs, and outputs. It persists across `docker compose down` and image rebuilds, and is large — it's excluded from any distribution zip. The `comfyui.db` SQLite file lives in `workspace/user/`.
- **Filename safety:** `slug()` produces ASCII-only prefixes for ComfyUI output paths; `safe_name()` preserves Korean/Unicode for user-facing filenames and project names. Use the right one.
- **Path traversal:** media/download/delete endpoints wrap user input in `os.path.basename()` before joining to a category dir — preserve that when adding file endpoints.
- **GGUF (Q5) is the default** (runs in ~7–8GB RAM with swap); fp16 needs RAM 16GB+. The smoke/CLI scripts default to the GGUF model names.
- **Editor port mapping is `8090:8080`** because Windows already uses 8080 on the host; the container always listens on 8080.
- **eGPU drops are the #1 operational failure here — not a code bug.** The RTX 3060 is external over USB4 and intermittently drops, especially under load. Tell-tale signs: `nvidia-smi` reports *"GPU access blocked by the operating system"*, the container sees *"no adapters were found"*, ComfyUI crash-loops with *"Found no NVIDIA driver"* (exit 1), the editor banner flips to 🔴, and generate jobs fail with `ComfyUI history 조회 실패`. Recovery is on the **Windows host**: Device Manager → RTX 3060 → disable/enable (or run `C:\Users\lenovo\reset_egpu.ps1` elevated, or reboot Windows — avoid Fast Startup), then `wsl --shutdown` and `docker compose up -d`. Verify with `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`. **Editing/export/thumbnails keep working while the GPU is down — only clip generation needs it.**
