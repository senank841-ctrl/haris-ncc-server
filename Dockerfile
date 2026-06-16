FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y ffmpeg nodejs npm && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY download_server.py cookies.txt ./
EXPOSE 3001
CMD ["python", "download_server.py"]