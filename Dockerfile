FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/logs

ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Seoul

CMD ["python", "-m", "src.main"]
