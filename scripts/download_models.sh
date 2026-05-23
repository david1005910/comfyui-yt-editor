#!/bin/bash
# WAN 2.2 5B 모델 다운로드 (GGUF 권장 — 저RAM/12GB VRAM 환경).
# 사용: bash scripts/download_models.sh        # GGUF (기본)
#       bash scripts/download_models.sh fp16    # 추가로 fp16 원본도 받기 (고RAM 환경)
set -e
M=workspace/models
mkdir -p "$M"/{diffusion_models,unet,text_encoders,vae,loras}

dl(){ # url dest
  if [ -f "$2" ]; then echo "  이미 있음: $(basename "$2")"; else
    echo "  받는 중: $(basename "$2")"; wget -c -q --show-progress -O "$2" "$1"; fi
}

echo "[GGUF] WAN 2.2 5B (저RAM 권장)"
dl "https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF/resolve/main/Wan2.2-TI2V-5B-Q5_K_M.gguf" \
   "$M/unet/Wan2.2-TI2V-5B-Q5_K_M.gguf"
dl "https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q5_K_M.gguf" \
   "$M/text_encoders/umt5-xxl-encoder-Q5_K_M.gguf"

echo "[VAE] (공통, 필수)"
dl "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors" \
   "$M/vae/wan2.2_vae.safetensors"

if [ "$1" = "fp16" ]; then
  echo "[fp16] 원본 (고RAM 환경 전용, ~16GB)"
  dl "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" \
     "$M/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"
  dl "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
     "$M/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
fi

echo "완료. 받은 파일:"; ls -lh "$M"/unet/*.gguf "$M"/text_encoders/* "$M"/vae/*.safetensors 2>/dev/null
