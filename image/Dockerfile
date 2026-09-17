# fief-relay hub image.
# Alpine (has /bin/sh) + pinned chisel binary verified by checksum.
# All config comes from env at runtime — no secrets baked in.
FROM docker.io/library/alpine:3.21

ARG CHISEL_VERSION=1.12.0

RUN wget -q -O /tmp/chisel.gz \
      "https://github.com/jpillora/chisel/releases/download/v${CHISEL_VERSION}/chisel_${CHISEL_VERSION}_linux_amd64.gz" \
 && wget -q -O /tmp/checksums.txt \
      "https://github.com/jpillora/chisel/releases/download/v${CHISEL_VERSION}/chisel_${CHISEL_VERSION}_checksums.txt" \
 && HASH="$(grep 'linux_amd64.gz' /tmp/checksums.txt | awk '{print $1}')" \
 && echo "${HASH}  /tmp/chisel.gz" | sha256sum -c - \
 && gunzip -c /tmp/chisel.gz > /usr/local/bin/chisel \
 && chmod +x /usr/local/bin/chisel \
 && rm /tmp/chisel.gz /tmp/checksums.txt \
 && chisel --version

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 10000

ENTRYPOINT ["/entrypoint.sh"]
