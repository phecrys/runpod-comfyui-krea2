# RunPod 커스텀 템플릿: ComfyUI + Krea 2 + Civitai 자동 설치

파드를 켜면 아래 순서로 **자동 준비**됩니다.

1. 최신 ComfyUI(+ Manager) 실행 → `8188` 포트
2. `/workspace/models.yaml` 에 적힌 모델을 Civitai / Hugging Face 에서 내려받음 (이미 받은 건 건너뜀)
3. Krea2 + LoRA 워크플로(`krea2_lora_t2i.json`)가 ComfyUI 워크플로 목록에 들어 있음

실행 중에도 `add-model <Civitai 링크>` 한 줄로 모델을 바로 추가할 수 있습니다 (재시작 불필요).

```
├─ Dockerfile                    # CUDA 12.8 + PyTorch + 최신 ComfyUI 이미지
├─ .github/workflows/build.yml   # GitHub 에 올리면 자동으로 이미지 빌드 (로컬 Docker 불필요)
├─ config/models.yaml            # ★ 받을 모델 목록 (기본: Krea2 Turbo + 텍스트인코더 + VAE)
├─ scripts/start.sh              # 컨테이너 시작 스크립트
├─ scripts/provision.py          # Civitai / HF 다운로더 (자동 폴더 분류, 이어받기, 중복 건너뛰기)
├─ scripts/add-model             # 실행 중 모델 추가 명령
├─ workflows/krea2_lora_t2i.json # Krea2 + LoRA 2개 체인 워크플로
└─ tests/                        # 다운로더 단위 테스트
```

---

## 0. ⚠️ 먼저: Civitai API 키 재발급

API 키를 채팅에 붙여넣으셨으므로 **Civitai → Account Settings → API Keys 에서 기존 키를 삭제하고 새로 발급**하세요.
이 프로젝트 파일 어디에도 키는 들어 있지 않습니다. 키는 3단계에서 RunPod **Secret** 으로만 넣습니다
(이미지나 GitHub 에 키가 들어가면 공개될 수 있습니다).

---

## 1. GitHub 에 올리기 (이미지 자동 빌드)

1. GitHub 에서 새 저장소 생성 (예: `runpod-comfyui-krea2`, Public/Private 무관)
2. 이 폴더 전체를 업로드
   - **먼저 `_github` 폴더 이름을 `.github` 로 바꾸세요** (자동 빌드 설정 파일이 들어 있음.
     Windows 탐색기에서 이름 바꾸기가 막히면 이름을 `.github.` 처럼 끝에 점을 붙여 입력하면 됩니다)
   - 가장 쉬운 방법: GitHub Desktop → *Add local repository* → 이 폴더 → Publish
   - 또는 웹에서 *Add file → Upload files* 로 올린 뒤, *Actions → set up a workflow yourself* 에
     `_github/workflows/build.yml` 내용을 붙여넣고 파일명을 `build.yml` 로 저장
3. 저장소의 **Actions** 탭에서 `build-image` 가 초록색으로 끝날 때까지 대기 (약 15~25분)
4. 프로필 → **Packages** → `runpod-comfyui-krea2` → *Package settings* → **Change visibility → Public**
   (Private 로 두려면 RunPod 템플릿에 *Container Registry Credentials* 로 GitHub 토큰을 등록해야 함)

결과 이미지 주소: `ghcr.io/<깃허브아이디>/runpod-comfyui-krea2:latest`

> ComfyUI 를 최신으로 다시 받고 싶을 때: Actions → build-image → *Run workflow* (원하면 `comfyui_ref` 에 태그 지정).
> Krea 2 의 `krea2` 텍스트인코더 타입은 최신(Nightly) ComfyUI 에만 있어서 기본값을 `master` 로 두었습니다.

---

## 2. RunPod Secret 등록

RunPod 콘솔 → **Secrets** → *Create Secret*

| 이름 | 값 |
|---|---|
| `civitai_token` | 새로 발급한 Civitai API 키 |
| `hf_token` (선택) | 게이트된 Hugging Face 모델을 쓸 때만 |

---

## 3. 템플릿 만들기

RunPod 콘솔 → **My Templates → New Template**

| 항목 | 값 |
|---|---|
| Template Name | `ComfyUI Krea2` |
| Template Type | Pod |
| Container Image | `ghcr.io/<깃허브아이디>/runpod-comfyui-krea2:latest` |
| Container Start Command | (비워 두기) |
| Container Disk | `30 GB` |
| Volume Disk | `100 GB` (모델 보관용. Network Volume 을 쓸 거면 0) |
| Volume Mount Path | `/workspace` |
| Expose HTTP Ports | `8188,8888` |
| Expose TCP Ports | `22` |

**Environment Variables**

| Key | Value | 설명 |
|---|---|---|
| `CIVITAI_TOKEN` | `{{ RUNPOD_SECRET_civitai_token }}` | 필수 |
| `HF_TOKEN` | `{{ RUNPOD_SECRET_hf_token }}` | 선택 |
| `JUPYTER_PASSWORD` | 원하는 비밀번호 | 설정 시 8888 포트에 JupyterLab(파일 업로드·터미널) |
| `MODELS` | 예) `civitai:1234567 civitai:2345678@loras` | 선택 — 파드마다 추가로 받을 모델 |
| `PROVISION_WAIT` | `0` | `1` 이면 모델 다 받은 뒤 ComfyUI 시작 |
| `DOWNLOAD_CONCURRENCY` | `3` | 동시 다운로드 수 |
| `COMFY_ARGS` | (비움) | ComfyUI 추가 인자 (예: `--highvram`) |
| `MODELS_CONFIG_URL` | (비움) | models.yaml 을 원격 URL(Gist raw 등)에서 받아 사용 |

> **Network Volume 권장**: 같은 데이터센터의 Network Volume(예: 150GB)을 만들어 파드에 붙이면
> 모델을 한 번만 받고 GPU 를 바꿔도 재사용됩니다. 매번 수십 GB 를 다시 받지 않아 시작이 빨라집니다.

---

## 4. 파드 실행

1. *Deploy* → GPU 선택 → 방금 만든 템플릿 선택
   - Krea 2 는 12B 모델입니다. FP8 기준 **24GB(RTX 4090/L4/A5000)** 에서 동작, **32~48GB(RTX 5090/L40S/A6000)** 가 여유롭습니다.
2. *Connect* → **HTTP 8188** → ComfyUI
3. 처음엔 모델이 백그라운드로 받아지는 중입니다. 진행상황: JupyterLab 터미널이나 SSH 에서
   ```bash
   tail -f /workspace/logs/provision.log
   ```
   “모델 프로비저닝 끝” 이 뜨면 ComfyUI 화면에서 **R** 키 → 모델 목록 새로고침
4. 왼쪽 *Workflows* → `krea2_lora_t2i.json` 열기 → *Run*
   (공식 워크플로는 *Template Library* 에서 “Krea-2” 검색)

---

## 5. 원하는 Civitai 모델 넣는 법 (3가지)

### 버전 ID 찾기
Civitai 모델 페이지 URL 의 `?modelVersionId=XXXXXXX` 가 버전 ID 입니다. URL 을 그대로 붙여 넣어도 됩니다.
(버전 없이 `https://civitai.com/models/111` 만 쓰면 **최신 버전**을 받습니다.)

### ① 실행 중 즉시 추가 — `add-model`
JupyterLab 터미널 / SSH 에서:
```bash
add-model "https://civitai.com/models/111111?modelVersionId=2222222"   # 체크포인트 → 자동 분류
add-model 3333333                                                     # LoRA → loras/
add-model 4444444 --dir loras/krea2 --name my_style.safetensors       # 폴더/파일명 지정
add-model https://civitai.com/models/555555 --file fp8                # 파일 여러 개 중 fp8 선택
```
받은 뒤 ComfyUI 에서 **R**. 기본적으로 `/workspace/models.yaml` 에도 기록돼 다음 파드에서 자동 설치됩니다.

### ② 고정 목록 — `/workspace/models.yaml`
JupyterLab 에서 파일을 열어 `models:` 아래에 추가:
```yaml
  - civitai: 2222222                 # Krea2 파인튜닝 체크포인트
  - civitai: 3333333                 # LoRA
    dir: loras/krea2
```
저장 후 `python /opt/krea2/provision.py --only models` (또는 파드 재시작).

### ③ 템플릿/파드 환경변수 — `MODELS`
템플릿을 고치지 않고 파드마다 다르게: `MODELS=civitai:2222222 civitai:3333333@loras`

### 자동 폴더 분류
| Civitai 타입 | 저장 위치 | ComfyUI 노드 |
|---|---|---|
| LORA / LoCon / DoRA | `models/loras` | LoraLoaderModelOnly |
| Checkpoint (DiT 가중치만) — **Krea2 파인튜닝 대부분** | `models/diffusion_models` | Load Diffusion Model (UNETLoader) |
| Checkpoint (VAE·텍스트인코더 포함 통짜) | `models/checkpoints` | Load Checkpoint |
| VAE / TextualInversion | `models/vae` / `models/embeddings` | |

체크포인트는 파일 헤더를 직접 열어 구조를 보고 판단하므로, 업로더가 타입을 대충 적어도 맞는 폴더로 갑니다.
Krea2 파인튜닝은 보통 **확산 모델만** 들어 있으므로 기본 텍스트인코더(`qwen3vl_4b`)와 VAE(`qwen_image_vae`)는 계속 필요합니다.

---

## 6. 워크플로 기본값 (`krea2_lora_t2i.json`)

- Turbo: **8 steps / CFG 1.0 / euler / simple**, 1024×1024
- RAW(비증류) 기반 체크포인트: steps 30~50, CFG 3~3.5 로 올리세요
- LoRA 노드 2개는 기본 **우회(보라색)** 상태 → 노드 선택 후 **Ctrl+B** 로 켜고 파일 선택
- 공식 Krea-2 Turbo 템플릿(프롬프트 확장·해상도 선택기 포함)과 결과가 다르면 그쪽 설정을 기준으로 맞추세요

---

## 7. 문제 해결

| 증상 | 해결 |
|---|---|
| `type: 'krea2' not in list` | ComfyUI 구버전 → Actions 에서 이미지 재빌드 후 파드 재생성 |
| Civitai 다운로드 실패 / “safetensors 형식이 아닙니다” | `CIVITAI_TOKEN` Secret 확인, 성인/로그인 필요 모델이면 토큰 필수 |
| 검은 이미지 | 텍스트인코더 fp8 → bf16 버전으로 교체해 보기 (일부 사용자 보고) |
| 새 모델이 목록에 없음 | ComfyUI 화면에서 **R**, 로그 `/workspace/logs/provision.log` 확인 |
| 디스크 부족 | Volume Disk 늘리기, `/workspace/models/.downloads` 의 잔여 `.part` 삭제 |
| GHCR 이미지 pull 실패 | 패키지 Public 전환 또는 템플릿에 Registry Credentials 등록 |

로그 위치: `/workspace/logs/{provision,comfyui,jupyter}.log`

---

## 개발 메모

- 다운로더 테스트: `python3 tests/test_provision.py`
- 워크플로 재생성: `python3 tools/make_workflow.py workflows/krea2_lora_t2i.json`
- 모델은 이미지에 굽지 않습니다(이미지 크기·빌드 시간 절약, Civitai 모델 교체 자유). 항상 같은 모델을 쓰고
  콜드스타트를 줄이고 싶다면 Network Volume 을 쓰는 것이 가장 효율적입니다.
