FROM python:3.10-slim

# install ffmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

CMD ["python", "main.py"]
