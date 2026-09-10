FROM node:22-bookworm-slim AS node
FROM python:3.14-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && apt-get update && apt-get install -y --no-install-recommends socat ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @openai/codex@0.153.4
COPY . /build
RUN pip install --no-cache-dir /build && rm -rf /build
COPY dev/local-agent/start.sh /usr/local/bin/start-agent
WORKDIR /state
ENTRYPOINT ["/usr/local/bin/start-agent"]
