# Minimal herdr-on-AgentCore smoke test image.
# Goal: prove herdr runs as a background server inside the microVM and stays
# attachable via InvokeAgentRuntimeCommandShell (agentcore exec --it / open_shell).

FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

# Install herdr via the official installer (pulls prebuilt binary for this arch),
# then relocate to /usr/local/bin (world-readable/executable). The installer
# defaults to ~/.local/bin which, run as root during build, is /root/.local/bin —
# mode 700, unreachable by the non-root `agent` user that actually runs the
# container process. Learned this the hard way in the smoke test.
RUN curl -fsSL https://herdr.dev/install.sh | sh -s -- --yes \
    && mv /root/.local/bin/herdr /usr/local/bin/herdr \
    && chmod 755 /usr/local/bin/herdr

RUN useradd -m -u 1000 agent
COPY --chown=agent:agent entrypoint.sh /app/entrypoint.sh
COPY --chown=agent:agent healthcheck.py /app/healthcheck.py
RUN chmod +x /app/entrypoint.sh

USER agent
WORKDIR /home/agent
ENV HOME=/home/agent

EXPOSE 8080
CMD ["/app/entrypoint.sh"]
