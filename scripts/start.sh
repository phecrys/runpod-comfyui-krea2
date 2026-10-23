#!/usr/bin/env bash
# =====================================================================
#  컨테이너 시작 스크립트
#   1) /workspace(볼륨)에 models / input / output / user 폴더를 만들고 ComfyUI 와 연결
#   2) SSH / JupyterLab (선택) 시작
#   3) 커스텀 노드 + 워크플로 설치 (동기, 빠름)
#   4) 모델 다운로드 (기본: 백그라운드 → ComfyUI 는 바로 뜸, 완료 후 화면에서 'R' 새로고침)
#   5) ComfyUI 실행
# =====================================================================
set -uo pipefail

WS="${WORKSPACE_DIR:-/workspace}"
COMFY="${COMFYUI_DIR:-/opt/ComfyUI}"
APP="${APP_DIR:-/opt/krea2}"
LOG_DIR="$WS/logs"

log() { echo "[start] $(date '+%H:%M:%S') $*"; }

mkdir -p "$WS" "$LOG_DIR"

# ---------------------------------------------------------------------
# 1) 영구 폴더 연결 (볼륨이 없어도 동작하지만, 그 경우 파드 종료 시 사라짐)
# ---------------------------------------------------------------------
link_dir() {   # link_dir <ComfyUI 내부 폴더> <볼륨 폴더>
  local src="$1" dst="$2"
  mkdir -p "$dst"
  if [ -d "$src" ] && [ ! -L "$src" ]; then
    cp -rn "$src"/. "$dst"/ 2>/dev/null || true   # 기본 파일/하위폴더 시드 (덮어쓰지 않음)
    rm -rf "$src"
  fi
  ln -sfn "$dst" "$src"
}

link_dir "$COMFY/models" "$WS/models"
link_dir "$COMFY/input"  "$WS/input"
link_dir "$COMFY/output" "$WS/output"
link_dir "$COMFY/user"   "$WS/user"

for d in checkpoints diffusion_models text_encoders clip vae loras embeddings \
         upscale_models controlnet clip_vision unet; do
  mkdir -p "$WS/models/$d"
done

# 기본 models.yaml 을 볼륨에 복사 (이미 있으면 사용자의 수정본 유지)
if [ ! -f "$WS/models.yaml" ]; then
  cp "$APP/config/models.yaml" "$WS/models.yaml"
  log "기본 models.yaml 을 $WS/models.yaml 에 복사했습니다. 이 파일을 수정해 모델을 관리하세요."
fi

# ---------------------------------------------------------------------
# 2) SSH (RunPod 계정에 공개키가 등록돼 있으면 PUBLIC_KEY 가 자동 주입됨)
# ---------------------------------------------------------------------
if [ -n "${PUBLIC_KEY:-}" ]; then
  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  echo "$PUBLIC_KEY" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
  ssh-keygen -A >/dev/null 2>&1
  service ssh start >/dev/null 2>&1 && log "SSH 시작 (포트 22)"
fi

# JupyterLab: JUPYTER_PASSWORD 가 설정된 경우에만 실행 (비번 없는 공개 노출 방지)
if [ -n "${JUPYTER_PASSWORD:-}" ]; then
  nohup jupyter lab --allow-root --no-browser --ip=0.0.0.0 --port=8888 \
        --ServerApp.token="$JUPYTER_PASSWORD" --ServerApp.allow_origin='*' \
        --ServerApp.root_dir="$WS" > "$LOG_DIR/jupyter.log" 2>&1 &
  log "JupyterLab 시작 (포트 8888, 비밀번호 = JUPYTER_PASSWORD)"
fi

# ---------------------------------------------------------------------
# 3) 커스텀 노드 / 워크플로 (ComfyUI 시작 전에 설치해야 로드됨)
# ---------------------------------------------------------------------
python "$APP/provision.py" --only nodes,workflows 2>&1 | tee -a "$LOG_DIR/provision.log"

# ---------------------------------------------------------------------
# 4) 모델 다운로드
# ---------------------------------------------------------------------
if [ "${SKIP_MODEL_DOWNLOAD:-0}" = "1" ]; then
  log "SKIP_MODEL_DOWNLOAD=1 → 모델 다운로드 건너뜀"
elif [ "${PROVISION_WAIT:-0}" = "1" ]; then
  log "모델 다운로드 완료까지 대기 후 ComfyUI 를 시작합니다 (PROVISION_WAIT=1)"
  python "$APP/provision.py" --only models 2>&1 | tee -a "$LOG_DIR/provision.log"
else
  log "모델 다운로드를 백그라운드로 시작합니다. 진행상황: tail -f $LOG_DIR/provision.log"
  ( python "$APP/provision.py" --only models 2>&1 | tee -a "$LOG_DIR/provision.log" ) &
fi

# ---------------------------------------------------------------------
# 5) ComfyUI
# ---------------------------------------------------------------------
EXTRA=""
[ -f /opt/.manager_builtin ] && EXTRA="--enable-manager"

cd "$COMFY"
log "ComfyUI 시작 (포트 8188)  args: $EXTRA ${COMFY_ARGS:-}"
# shellcheck disable=SC2086
python main.py --listen 0.0.0.0 --port 8188 $EXTRA ${COMFY_ARGS:-} 2>&1 | tee -a "$LOG_DIR/comfyui.log"

log "ComfyUI 가 종료되었습니다. 디버깅을 위해 컨테이너는 유지합니다 (로그: $LOG_DIR/comfyui.log)"
sleep infinity
