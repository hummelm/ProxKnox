# proxknox-pxe — PXE server in a container (replaces the pxe-server VM)
# Same role as the pxe-server VM of the KVM variant: DHCP + TFTP + DNS
# (dnsmasq) on the pxe-lab segment (192.168.200.0/24), HTTP for the preseed
# and files (http://192.168.200.2/...), NAT of the segment to the Internet,
# reception of the debian-installer syslog (UDP 10514) and SSH client for
# the Proxmox VE conversion of the client (run via docker exec).
#
# build: docker build -t proxknox-pxe:latest pxe/container
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        dnsmasq pxelinux syslinux-common syslinux-utils tftp-hpa \
        openssh-client \
        wget ca-certificates \
        python3 python3-minimal \
        iptables iproute2 procps \
    && rm -rf /var/lib/apt/lists/*

# syslog-listener: debian-installer log of the client (log_host/log_port)
COPY syslog-listener.py /usr/local/bin/syslog-listener.py
# entrypoint: NAT (ip_forward + MASQUERADE), TFTP root, dnsmasq, HTTP :80
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/syslog-listener.py \
    && mkdir -p /srv/tftp /srv/http /srv/logs

EXPOSE 67/udp 69/udp 80/tcp 10514/udp 53/udp 53/tcp

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
