# =====================================================================
#  RunPod ComfyUI + Krea 2 커스텀 템플릿 이미지
#  - 모델은 이미지에 굽지 않습니다. 파드가 시작될 때 /workspace(볼륨)에
#    models.yaml 목록대로 Civitai / Hugging Face 에서 내려받습니다.
#  - Krea 2 의 CLIPLoader type "krea2" 는 최신(Nightly) ComfyUI 가 필요하므로
#    기본값으로 ComfyUI master 브랜치를 사용합니다.
# =====================================================================
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    COMFYUI_DIR=/opt/ComfyUI

# ---- 시스템 패키지 -------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        git git-lfs aria2 curl wget ca-certificates tini \
        ffmpeg libgl1 libglib2.0-0 openssh-server nano less rsync \
    && rm -rf /var/lib/apt/lists/*

# ---- Python 3.12 (uv) ----------------------------------------------------
COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /usr/local/bin/
RUN uv venv /opt/venv --python 3.12 --seed

# ---- PyTorch (CUDA 12.8: RTX 4090 / 5090 / L40S / A100 / H100 모두 지원) --
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
RUN uv pip install torch torchvision torchaudio --index-url ${TORCH_INDEX_URL}

# ---- ComfyUI -------------------------------------------------------------
# 특정 버전에 고정하고 싶으면 빌드 인자 COMFYUI_REF 에 태그/커밋을 넣으세요 (예: v0.26.1)
ARG COMFYUI_REF=master
RUN git clone https://github.com/Comfy-Org/ComfyUI.git ${COMFYUI_DIR} \
    && cd ${COMFYUI_DIR} && git checkout ${COMFYUI_REF} \
    && uv pip install -r requirements.txt

# ---- ComfyUI-Manager -----------------------------------------------------
# 최신 ComfyUI 는 manager_requirements.txt + --enable-manager 방식,
# 구버전은 custom_nodes 에 클론하는 방식. 둘 중 가능한 쪽으로 설치합니다.
RUN cd ${COMFYUI_DIR} \
    && if [ -f manager_requirements.txt ]; then \
         uv pip install -r manager_requirements.txt && touch /opt/.manager_builtin ; \
       else \
         git clone --depth 1 https://github.com/ltdrdata/ComfyUI-Manager.git custom_nodes/ComfyUI-Manager \
         && uv pip install -r custom_nodes/ComfyUI-Manager/requirements.txt ; \
       fi

# ---- 프로비저닝 도구 / JupyterLab ------------------------------------------
RUN uv pip install pyyaml requests "huggingface_hub[hf_xet]" jupyterlab

COPY scripts/   /opt/krea2/
COPY config/    /opt/krea2/config/
COPY workflows/ /opt/krea2/workflows/
RUN chmod +x /opt/krea2/start.sh /opt/krea2/add-model \
    && ln -sf /opt/krea2/add-model /usr/local/bin/add-model \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config

WORKDIR /workspace
EXPOSE 8188 8888 22

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/krea2/start.sh"]
