#!/bin/bash
# setup_environment.sh

# 시스템 업데이트
sudo apt update && sudo apt upgrade -y

# eBPF 개발 도구
sudo apt install -y \
    linux-headers-$(uname -r) \
    linux-tools-$(uname -r) \
    linux-tools-generic \
    clang \
    llvm \
    libbpf-dev \
    bpfcc-tools \
    python3-bpfcc

# Python 개발 환경
sudo apt install -y \
    python3.10 \
    python3.10-dev \
    python3-pip \
    python3-venv

# 빌드 도구
sudo apt install -y \
    build-essential \
    pkg-config \
    libelf-dev \
    zlib1g-dev

# 모니터링 도구
sudo apt install -y \
    bpftrace \
    perf-tools-unstable



# 프로젝트 디렉토리 생성
mkdir -p ~/eBPF/ebpf-metrics-collector2
cd ~/eBPF/ebpf-metrics-collector2

echo 현재 : `pwd`
# 가상환경 생성
python3 -m venv venv --system-site-packages
source venv/bin/activate

# 필수 Python 패키지 설치
pip install --upgrade pip
pip install -r requirements.txt