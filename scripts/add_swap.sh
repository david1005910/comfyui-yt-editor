#!/bin/bash
# WSL에 스왑 추가 (재시작 불필요). sudo 로 실행: sudo bash scripts/add_swap.sh
set -e
SWAP=/swapfile
SIZE_GB=12

if swapon --show 2>/dev/null | grep -q "$SWAP"; then
  echo "[i] $SWAP 이미 활성화됨"; swapon --show; exit 0
fi

echo "[1] ${SIZE_GB}GB 스왑파일 생성 (/swapfile)..."
if ! fallocate -l ${SIZE_GB}G "$SWAP" 2>/dev/null; then
  echo "    fallocate 실패 → dd 로 생성 (조금 더 걸림)"
  rm -f "$SWAP"
  dd if=/dev/zero of="$SWAP" bs=1M count=$((SIZE_GB*1024)) status=progress
fi
chmod 600 "$SWAP"
echo "[2] mkswap..."; mkswap "$SWAP" >/dev/null
echo "[3] swapon..."; swapon "$SWAP"
echo "[4] 재부팅에도 유지되도록 /etc/fstab 등록..."
grep -q "^$SWAP " /etc/fstab 2>/dev/null || echo "$SWAP none swap sw 0 0" >> /etc/fstab

echo "=== 완료 ==="
swapon --show
free -h
