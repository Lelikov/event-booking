# Build context MUST be the monorepo root (event-schemas is a relative path dependency):
#   docker build -f event-booking/Dockerfile .
ARG BASE_IMAGE="python:3.14.0"

FROM ${BASE_IMAGE} AS base

ENV APP_PATH="/app/event-booking"
ENV PATH="${APP_PATH}/.venv/bin:${PATH}"

WORKDIR ${APP_PATH}

FROM base AS deps

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --upgrade uv==0.11.3

# Mirror the monorepo layout so the ../event-schemas editable path in uv.lock resolves.
COPY event-schemas /app/event-schemas
COPY event-booking/pyproject.toml event-booking/uv.lock ${APP_PATH}/
RUN uv sync --frozen --no-install-project --no-dev

FROM deps AS development

COPY event-booking/event_booking ${APP_PATH}/event_booking

EXPOSE 8888

ENTRYPOINT ["uvicorn", "event_booking.main:app", "--host", "0.0.0.0", "--port", "8888"]
