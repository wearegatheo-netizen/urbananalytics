# 연속지적 위치도 브릿지 — 컨테이너 배포용 (Render/Fly/Railway 등)
FROM python:3.12-slim

# 한글 라벨 렌더용 폰트(나눔). PIL이 절대경로로 직접 로드하므로 fontconfig/fc-cache 불필요.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-nanum fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# 호스트가 주입하는 PORT를 사용(없으면 8788). main()이 PORT 환경변수면 0.0.0.0 바인딩.
ENV PORT=8788
EXPOSE 8788
CMD ["python", "cadastre_bridge_server.py"]
