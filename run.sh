#!/usr/bin/env bash
# 🎬 AI 유튜브 영상 편집기 — 원클릭 설치/실행 스크립트
# 사용법:  bash run.sh
set -e
cd "$(dirname "$0")"

echo "🎬 AI 유튜브 영상 편집기 (WAN 2.2 · ComfyUI)"
echo "================================================================"

# 1) Docker 확인 ------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "❌ 'docker' 명령을 찾을 수 없습니다."
  echo "   • Windows: Docker Desktop 설치 후 Settings → Resources → WSL Integration 켜기"
  echo "   • Linux:   https://docs.docker.com/engine/install/"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker 데몬에 연결할 수 없습니다. Docker Desktop을 먼저 실행하세요."
  exit 1
fi
echo "✅ docker $(docker --version | awk '{print $3}' | tr -d ,)"

# 2) GPU 확인 (선택) -------------------------------------------------
if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1; then
  echo "✅ NVIDIA GPU 사용 가능 — AI 생성 가능"
else
  echo "⚠️  GPU 미감지 — 편집/내보내기/썸네일은 가능하지만 AI 생성은 불가."
  echo "    (Windows용 NVIDIA 드라이버 + Docker Desktop의 GPU 통합을 확인하세요)"
fi

# 3) 모델 확인/다운로드 ----------------------------------------------
GGUF="workspace/models/unet/Wan2.2-TI2V-5B-Q5_K_M.gguf"
if [ -f "$GGUF" ]; then
  echo "✅ WAN 2.2 모델 이미 있음"
else
  echo "⬇️  WAN 2.2 5B GGUF 모델이 없습니다 (~8GB, 최초 1회 다운로드)."
  read -r -p "    지금 다운로드할까요? [Y/n] " ans || ans="Y"
  if [ "$ans" != "n" ] && [ "$ans" != "N" ]; then
    bash scripts/download_models.sh
  else
    echo "    건너뜀 — 나중에 직접:  bash scripts/download_models.sh"
  fi
fi

# 4) 스왑 권장 (RAM ≤ 16GB & 스왑 없음) -------------------------------
RAM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}')
if [ -n "$RAM_GB" ] && [ "$RAM_GB" -le 16 ] && ! swapon --show 2>/dev/null | grep -q swapfile; then
  echo "⚠️  시스템 RAM ${RAM_GB}GB — 모델 로딩 OOM 방지를 위해 12GB 스왑 권장:"
  echo "       sudo bash scripts/add_swap.sh"
fi

# 5) 빌드 & 기동 -----------------------------------------------------
echo "🐳 docker compose 빌드 & 기동 중... (첫 실행은 수 분 소요)"
docker compose up -d --build

echo "================================================================"
echo "✅ 실행 완료!"
echo "   📝 편집기 :  http://localhost:8090"
echo "   🧩 ComfyUI:  http://localhost:8188"
echo ""
echo "   로그 보기:  docker compose logs -f editor"
echo "   중지     :  docker compose stop"
echo "   재시작   :  docker compose up -d"
