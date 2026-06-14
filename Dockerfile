# Self-contained build (context = this service repo root):
#   docker build -t event-booking .
ARG BASE_IMAGE="python:3.14.0"

FROM ${BASE_IMAGE} AS base

ENV APP_PATH="/app/event-booking"
ENV PATH="${APP_PATH}/.venv/bin:${PATH}"

WORKDIR ${APP_PATH}

FROM base AS deps

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --upgrade uv==0.11.3

# event-schemas is a git dependency pinned in uv.lock; uv fetches it during sync.
COPY pyproject.toml uv.lock ${APP_PATH}/
RUN uv sync --frozen --no-install-project --no-dev

FROM deps AS development

COPY event_booking ${APP_PATH}/event_booking

EXPOSE 8888

ENTRYPOINT ["uvicorn", "event_booking.main:app", "--host", "0.0.0.0", "--port", "8888"]
