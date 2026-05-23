# ComfyUI updated for WAN 2.2 support, based on ai-dock latest-cuda.
# 번들 ComfyUI(v0.2.2)는 WAN 2.2 네이티브 노드가 없어, 빌드 시 최신 릴리스로 업데이트해 이미지에 굽는다.
# (재생성/재부팅에도 영구 유지되고 시작이 빠름)
FROM ghcr.io/ai-dock/comfyui:latest-cuda

# 업데이트할 ComfyUI ref (기본 master = 최신). 특정 버전 고정 시 build arg 로 지정.
# (ai-dock 기본 update 스크립트는 GitHub API 301 리다이렉트를 안 따라가 실패하므로 직접 수행)
ARG COMFYUI_REF=master

RUN cd /opt/ComfyUI && \
    git fetch origin "${COMFYUI_REF}" --tags && \
    git checkout "${COMFYUI_REF}" && \
    (git pull --ff-only || true) && \
    /opt/environments/python/comfyui/bin/pip install --no-cache-dir -r requirements.txt && \
    echo "ComfyUI updated to: $(git describe --tags --always)"

# ComfyUI-GGUF: 양자화(.gguf) 모델/인코더 로더 노드 (저RAM 환경용)
RUN cd /opt/ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/city96/ComfyUI-GGUF.git && \
    /opt/environments/python/comfyui/bin/pip install --no-cache-dir -r ComfyUI-GGUF/requirements.txt && \
    echo "ComfyUI-GGUF installed"
