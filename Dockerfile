# Linux에서 시험과 명령행 실행을 재현한다. 로컬 AI 모델은 넣지 않는다(모델 없이 해석하는 경로).
#   docker build -t ureonggaksi .
#   docker run --rm --network none ureonggaksi              # 시험·린트
#   docker run --rm --network none ureonggaksi scripts/demo.sh   # 합성 시연 명령행
# 기반 이미지는 태그와 함께 digest로 고정한다. 태그는 옮겨질 수 있다.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# CI(setup-uv)와 같은 uv 버전.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /bin/uv

# PySide6가 화면 없이(offscreen) 뜨는 데 필요한 라이브러리. CI의 Linux 단계와 같은 목록이다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libegl1 libxkbcommon0 libglib2.0-0 libdbus-1-3 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app
WORKDIR /app
RUN chown app:app /app
USER app

# 캐시를 이미지에 남기지 않는다(크기, 그리고 ruff가 /app 아래 캐시를 검사하지 않도록).
ENV UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    QT_QPA_PLATFORM=offscreen \
    PATH=/app/.venv/bin:$PATH

# 의존성 층을 먼저 만든다. 소스만 바뀌면 이 층은 다시 받지 않는다.
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --locked --extra dev --no-install-project

COPY --chown=app:app . .
RUN uv sync --locked --extra dev

CMD ["scripts/test.sh"]
