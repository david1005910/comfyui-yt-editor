# 🎬 AI 유튜브 영상 편집기 — 빠른 시작

WAN 2.2 5B(ComfyUI)로 **AI 영상 클립을 만들고**, 브라우저에서 **편집(트림·자막·전환·카드·BGM·썸네일)**해
**유튜브용 mp4(가로/세로 Shorts/정사각)**로 내보내는 풀스택 웹앱입니다.

---

## ✅ 준비물
- **Windows 11 + WSL2 + Docker Desktop** (Settings → Resources → **WSL Integration 켜기**)
  - 또는 Linux + Docker Engine
- **NVIDIA GPU + Windows용 NVIDIA 드라이버** (AI 생성용. *없어도 편집·내보내기는 됩니다*)
- 디스크 ~20GB (모델), 권장 GPU VRAM 8GB+

> 확인:  `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`

---

## 🚀 실행 (3단계)

```bash
# 1) 압축 풀기 (예: 홈으로)
unzip yt-ai-editor.zip -d ~/yt-ai-editor
cd ~/yt-ai-editor

# 2) 원클릭 실행 (모델 자동 다운로드 + 빌드 + 기동)
bash run.sh

# 3) 브라우저 열기
#    편집기 :  http://localhost:8090
#    ComfyUI:  http://localhost:8188
```

`run.sh`가 자동으로:
1. Docker / GPU 점검 → 2. 모델(~8GB) 없으면 다운로드 → 3. (저RAM이면) 스왑 안내 → 4. `docker compose up -d --build`

---

## 🖱️ 사용 순서 (편집기 탭)
1. **① 생성** — 프롬프트 입력 → *단일 클립* 또는 *긴 영상(구간 체이닝)* → 생성
2. **② 라이브러리** — 클립/오디오 관리, "＋ 타임라인"으로 편집에 추가
3. **③ 타임라인 & 내보내기** — 드래그 정렬, 트림·자막·볼륨·페이드·**전환효과**, **타이틀 카드**, 화면비(16:9 / 9:16 / 1:1) → mp4
4. **④ 썸네일** — 프레임 + 제목 → 1280×720 jpg
5. **💾 프로젝트** — 타임라인 저장/불러오기

---

## 🛠️ 수동 실행 (run.sh 대신)
```bash
bash scripts/download_models.sh     # 모델 (GGUF, ~8GB)
sudo bash scripts/add_swap.sh       # (RAM 16GB 이하) OOM 방지 스왑
docker compose up -d --build        # 빌드 & 기동
```

## 🧰 관리 명령
| 동작 | 명령 |
|------|------|
| 로그 보기 | `docker compose logs -f editor` / `... comfyui` |
| 중지 | `docker compose stop` |
| 재시작 | `docker compose up -d` |
| 코드 수정 후 재빌드 | `docker compose up -d --build` |
| 완전 삭제(모델 제외) | `docker compose down` |

## ❓ 문제 해결
| 증상 | 해결 |
|------|------|
| `docker: command not found` | Docker Desktop 실행 + WSL Integration 켜기 |
| 편집기 "🔴 ComfyUI 연결 안 됨" | `docker compose logs comfyui` 확인. 편집/내보내기는 그대로 가능 |
| ComfyUI `exit 137` (OOM) | `sudo bash scripts/add_swap.sh` 로 스왑 추가 |
| 모델이 안 보임 | `bash scripts/download_models.sh` 후 ComfyUI 새로고침 |
| 포트 충돌 | `docker-compose.yaml`의 `8090:8080` 왼쪽 숫자(호스트 포트)를 변경 |

자세한 내용은 **README.md** 참고.
