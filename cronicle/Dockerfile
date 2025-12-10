FROM soulteary/cronicle:0.9.80

# Uncomment if you have a patched comm.js for custom master/slave communication
# COPY comm.js /opt/cronicle/lib/comm.js

# Install common utilities needed by plugins
RUN apk add --no-cache \
    restic \
    openssh-client \
    smartmontools \
    sysstat \
    procps \
    util-linux \
    coreutils \
    bash
