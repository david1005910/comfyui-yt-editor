# 🎬 AI 유튜브 영상 편집기 (WAN 2.2 · ComfyUI · RTX 3060 eGPU)

로컬 GPU로 **AI 동영상 클립을 생성**하고, 브라우저에서 **본격 편집**해
**유튜브용 mp4(가로 16:9 / 세로 9:16 Shorts / 정사각)로 내보내는** 풀스택 웹앱입니다.

- **클립 생성**: WAN 2.2 5B (텍스트→영상 / 이미지→영상) — ComfyUI 백엔드
- **긴 영상**: 구간 체이닝(마지막 프레임→다음 시작 이미지)으로 5초+ 클립을 웹에서 바로
- **타임라인 편집**: 드래그 순서변경, 트림, 클립별 볼륨·페이드, **전환효과(크로스페이드/와이프 등)**,
  자막 스타일(위치·크기·색·박스, **한글+컬러 이모지** 지원), **타이틀/인트로 카드**
- **유튜브 마무리**: **썸네일 생성**(프레임+제목), 배경음악 페이드/믹스
- **프로젝트** 저장/불러오기 · 라이브러리 클립/오디오 관리
- **편집기**: FastAPI + ffmpeg 백엔드 + 탭형 단일 페이지 웹 UI
- 전부 Docker Compose 한 방으로 실행

```
┌─────────────┐   HTTP /prompt    ┌──────────────┐
│  editor     │ ────────────────▶ │  ComfyUI     │  (WAN 2.2, GPU)
│ :8080 웹UI  │ ◀──────────────── │  :8188       │
│ FastAPI+ffmpeg│   공유 볼륨(클립/출력)  └──────────────┘
└─────────────┘
```

## 1. 필요 환경
- Windows 11 + WSL2 + **Docker Desktop**(WSL2 백엔드, GPU 통합 ON)
- NVIDIA GPU + **Windows용 NVIDIA 드라이버** (WSL에 별도 리눅스 드라이버 설치 X — 패스스루)
- 디스크 ~20GB(모델), GPU VRAM 8GB+ 권장
- 확인: `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`

## 2. 설치 & 실행
```bash
# (1) 모델 다운로드 (GGUF, 저RAM 권장 — 약 8GB)
bash scripts/download_models.sh
#   고RAM(32GB+) 환경이면 원본 fp16도:  bash scripts/download_models.sh fp16

# (2) 시스템 RAM이 16GB 이하라면 스왑 추가 (OOM 방지) — sudo 필요, 재시작 불필요
sudo bash scripts/add_swap.sh

# (3) 빌드 & 기동 (첫 빌드는 ComfyUI 업데이트 포함 수 분)
docker compose up -d --build

# (4) 접속
#   편집기 :  http://localhost:8090
#   ComfyUI:  http://localhost:8188
```

### 라이브러리(생성물) 저장 위치 바꾸기 (선택)
생성된 클립·내보내기는 기본적으로 `./workspace/output` 에 저장됩니다.
용량 큰 별도 디스크에 두려면 `.env` 로 경로만 지정하면 됩니다:
```bash
cp .env.example .env
# .env 에서 LIBRARY_DIR 을 원하는 절대경로로 지정. 예:
#   LIBRARY_DIR=/mnt/e/comfyui-library   # WSL2에서 Windows E: 드라이브
docker compose up -d --force-recreate    # 새 경로로 다시 마운트
```
모델·입력 이미지는 그대로 ext4 에 유지됩니다. (이동식 USB 드라이브는 분리 시 마운트가 깨지므로 비권장.)

## 3. 사용법 (탭 순서대로)
1. **① 생성** — 프롬프트 입력 → *단일 클립* 또는 *긴 영상(구간 체이닝)* 선택 → 해상도/길이/스텝.
   이미지를 넣으면 이미지→영상(I2V). 긴 영상은 목표 길이(초)만 고르면 구간 수를 자동 계산해 이어붙입니다.
2. **② 라이브러리** — 생성/업로드된 클립·오디오 관리(미리보기·삭제). 클립의 "＋ 타임라인"으로 편집에 올림.
3. **③ 타임라인 & 내보내기**
   - 행을 **드래그**하거나 ▲▼로 순서 변경, ✕로 제거
   - 클립별 **트림**(시작/끝), **자막**(한글, 위치·크기·색·박스), **볼륨·페이드인/아웃**
   - 이전 클립과의 **전환효과**(없음/페이드/디졸브/와이프/슬라이드/원 등)와 전환 시간
   - **＋ 타이틀 카드**로 인트로/구분 카드(제목·부제·배경색) 삽입
   - **화면비**(16:9 / 9:16 Shorts / 1:1) · fps · 배경음악(볼륨·페이드·원본소리 유지) → **내보내기**(mp4)
4. **④ 썸네일** — 클립의 한 프레임 + 큰 제목/부제 → 1280×720 썸네일(jpg) 다운로드.
5. **💾 프로젝트** — 타임라인 구성·자막·전환·출력 설정을 저장/불러오기(영상 파일은 라이브러리에 보존).

## 4. 길이 / 성능 메모
- WAN 2.2 5B 단일 생성 최대 **121프레임(24fps=5초)**. 길이는 `4n+1` 값(49/81/121)으로 자동 보정.
- **긴 영상**은 편집기 ①탭에서 바로(구간 체이닝). CLI 예제는 `examples/chain_gen.py`.
- **GGUF(Q5)** 사용 시 7~8GB RAM에서도 동작(스왑 권장). fp16은 RAM 16GB+ 권장.
- 생성·전환효과 인코딩은 GPU/CPU 연산, 모델 로딩은 시스템 RAM/스왑에 의존.
- **편집/내보내기/썸네일은 GPU 없이도 동작** — eGPU가 빠져도 기존 클립 편집은 계속 가능.

## 5. 문제 해결
| 증상 | 원인 / 해결 |
|------|------|
| ComfyUI가 죽고 `exit 137` | 시스템 RAM OOM → `scripts/add_swap.sh`로 스왑 추가, GGUF 사용 |
| `502 Bad Gateway` | ComfyUI 재기동 중 — 잠시 후 재시도 |
| 편집기에 "ComfyUI 연결 안 됨" | `docker compose ps`로 comfyui 상태 확인, `docker compose logs comfyui` |
| 모델이 드롭다운에 없음 | `scripts/download_models.sh` 실행, ComfyUI 새로고침 |

## 6. 구성
```
docker-compose.yaml   두 서비스(comfyui, editor)
Dockerfile            ComfyUI: ai-dock + 최신 ComfyUI(WAN 2.2) + ComfyUI-GGUF
editor/               편집기 (app.py / comfy_client.py / static/ / Dockerfile)
workflows/            ComfyUI UI용 WAN 2.2 워크플로(GGUF/fp16)
scripts/              download_models.sh, add_swap.sh
examples/             gen.py(단일), chain_gen.py(이어붙이기) CLI 예제
workspace/            (실행 시 생성) 모델·입출력. 용량 큼 — 배포 zip엔 미포함
```
