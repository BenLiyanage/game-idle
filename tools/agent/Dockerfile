FROM ghcr.io/openai/codex-universal:latest

ARG CODEX_CLI_PACKAGE=@openai/codex@0.153.0-alpha.5

USER root
RUN npm install -g "${CODEX_CLI_PACKAGE}" \
    && npm cache clean --force

USER ubuntu
