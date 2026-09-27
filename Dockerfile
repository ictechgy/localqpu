# localqpu 컨테이너. 컨테이너 안에서는 외부에서 접속할 수 있도록 0.0.0.0에 바인딩한다.
# 호스트에서는 루프백에만 노출하는 것을 권장한다: docker run -p 127.0.0.1:8787:8787 ghcr.io/ictechgy/localqpu
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# Braket 흉내까지 쓸 수 있도록 선택 설치(braket)를 함께 넣는다(이미지는 Python 3.12라 설치 가능).
RUN pip install --no-cache-dir ".[braket]" \
    && useradd --create-home --uid 10001 localqpu
# 에뮬레이터는 파일을 쓰지 않으므로 권한 없는 사용자로 실행한다.
USER localqpu
EXPOSE 8787
# CI 서비스 컨테이너·docker compose가 준비 완료를 알 수 있도록 제어 API로 상태를 확인한다.
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/_localqpu/health', timeout=2)"]
CMD ["localqpu", "start", "--host", "0.0.0.0", "--port", "8787"]
