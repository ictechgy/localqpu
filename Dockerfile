# localqpu 컨테이너. 컨테이너 안에서는 외부에서 접속할 수 있도록 0.0.0.0에 바인딩한다.
# 호스트에서는 루프백에만 노출하는 것을 권장한다: docker run -p 127.0.0.1:8787:8787 localqpu
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
EXPOSE 8787
CMD ["localqpu", "start", "--host", "0.0.0.0", "--port", "8787"]
