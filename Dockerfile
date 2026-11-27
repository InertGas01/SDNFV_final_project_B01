FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    mininet \
    openvswitch-switch \
    openvswitch-common \
    iproute2 \
    iputils-ping \
    net-tools \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/openvswitch /var/log/openvswitch

RUN pip3 install --no-cache-dir os-ken networkx pytest

WORKDIR /workspace