# AI 유튜브 영상 편집기 백엔드 (FastAPI + ffmpeg)
FROM python:3.11-slim

# ffmpeg(libass) + 한글/CJK 폰트 + 컬러 이모지 폰트 + fontconfig(libass 폰트 폴백용)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fontconfig fonts-dejavu-core fonts-noto-cjk fonts-noto-color-emoji \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py comfy_client.py ./
COPY static ./static

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
