FROM python:3.13.1

WORKDIR /app

RUN apt-get update && apt-get -y install \
    libhunspell-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

ENV ANALYTICS_URL=""
ENV ANALYTICS_UUID=""

CMD ["python", "main.py"]
