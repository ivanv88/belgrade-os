FROM python:3.12-alpine

RUN apk del --purge curl wget git apk-tools 2>/dev/null; \
    addgroup -g 1000 runner && \
    adduser -u 1000 -G runner -D runner

WORKDIR /workspace
USER 1000:1000
