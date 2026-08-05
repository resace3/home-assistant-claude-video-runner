FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b
ARG BUILD_VERSION="dev"
ARG BUILD_ARCH="aarch64|amd64"
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.name="Personal Video Runner (Claude)" \
      io.hass.description="Privacy-first daily and weekly personal video generator driven by the Claude Code CLI" \
      io.hass.url="https://github.com/resace3/home-assistant-claude-video-runner" \
      org.opencontainers.image.title="Personal Video Runner (Claude)" \
      org.opencontainers.image.description="Privacy-first daily and weekly personal video generator driven by the Claude Code CLI" \
      org.opencontainers.image.url="https://github.com/resace3/home-assistant-claude-video-runner" \
      org.opencontainers.image.source="https://github.com/resace3/home-assistant-claude-video-runner" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core gosu && rm -rf /var/lib/apt/lists/*

# Node.js and the Claude Code CLI. Both are version-pinned; Node is additionally
# checksum-verified per architecture against the published nodejs.org SHASUMS256
# entry, matching how the base image and Python dependencies are pinned. The
# runner shells out to `claude -p`, so a floating `latest` would silently change
# storyboard behaviour between rebuilds.
ARG NODE_VERSION="22.23.2"
ARG NODE_SHA256_AMD64="d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307"
ARG NODE_SHA256_ARM64="fff4078c5def658577f92c88db7db3bc0072924bfb93fe52c1e744a54e94abb8"
ARG CLAUDE_CODE_VERSION="2.1.222"
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils; \
    case "$(dpkg --print-architecture)" in \
      amd64) node_arch="x64";   node_sha256="${NODE_SHA256_AMD64}" ;; \
      arm64) node_arch="arm64"; node_sha256="${NODE_SHA256_ARM64}" ;; \
      *) echo "unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/node.tar.xz \
      "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    echo "${node_sha256}  /tmp/node.tar.xz" | sha256sum -c -; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner \
      --exclude=CHANGELOG.md --exclude=LICENSE --exclude=README.md; \
    rm -f /tmp/node.tar.xz; \
    npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"; \
    npm cache clean --force; \
    node --version; npm --version; claude --version; \
    apt-get purge -y --auto-remove curl xz-utils; \
    rm -rf /var/lib/apt/lists/*

# Declare the text encoding rather than inheriting whatever the base image's
# locale resolves to. Narration carries curly quotes and em dashes, and the
# storyboard crosses a subprocess boundary as text.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app
COPY pyproject.toml requirements.lock README.md /app/
COPY src /app/src
COPY scripts/container-entrypoint.sh /usr/local/bin/container-entrypoint
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && \
    pip install --no-cache-dir --no-deps . && \
    useradd --system --uid 10001 --home-dir /data/personal_video_studio/claude-home --shell /usr/sbin/nologin runner && \
    chmod 0755 /usr/local/bin/container-entrypoint
ENTRYPOINT ["/usr/local/bin/container-entrypoint"]
CMD ["scheduler"]
