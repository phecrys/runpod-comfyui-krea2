#!/usr/bin/env python3
"""
models.yaml(+ 환경변수 MODELS)에 적힌 모델/커스텀노드/워크플로를 설치합니다.

  python provision.py                       # 전부
  python provision.py --only models         # 모델만
  python provision.py --only nodes,workflows

지원 소스
  civitai: 1234567                          # 모델 "버전" ID (페이지 URL의 ?modelVersionId=)
  civitai: https://civitai.com/models/111?modelVersionId=222
  civitai: https://civitai.com/models/111   # 버전 미지정 → 최신 버전
  url: https://huggingface.co/<org>/<repo>/resolve/main/<path>   (blob/ 링크도 OK)
  url: https://아무거나/파일.safetensors

환경변수
  CIVITAI_TOKEN   Civitai API 키 (RunPod Secret 으로 넣는 것을 권장)
  HF_TOKEN        (선택) 게이트/비공개 Hugging Face 저장소용
  MODELS          (선택) 템플릿/파드 단위로 추가할 모델. 공백·쉼표·줄바꿈 구분
                  형식: <소스>[@폴더]   예) civitai:123456@loras https://.../vae.safetensors@vae
  MODELS_CONFIG_URL  (선택) models.yaml 을 원격(URL)에서 받아 사용
  DOWNLOAD_CONCURRENCY 동시 다운로드 수 (기본 3)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

WS = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
COMFY = Path(os.environ.get("COMFYUI_DIR", "/opt/ComfyUI"))
APP = Path(__file__).resolve().parent
MODELS_DIR = WS / "models"
TMP_DIR = MODELS_DIR / ".downloads"
MANIFEST = MODELS_DIR / ".provisioned.json"
CIVITAI_API = "https://civitai.com/api/v1"

# Civitai 모델 type → ComfyUI models/ 하위 폴더
CIVITAI_TYPE_DIR = {
    "checkpoint": "@auto_checkpoint",   # 파일 내용을 보고 checkpoints / diffusion_models 결정
    "lora": "loras",
    "locon": "loras",
    "dora": "loras",
    "lycoris": "loras",
    "textualinversion": "embeddings",
    "vae": "vae",
    "controlnet": "controlnet",
    "upscaler": "upscale_models",
    "hypernetwork": "hypernetworks",
    "motionmodule": "animatediff_models",
}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[provision] {time.strftime('%H:%M:%S')} {redact(msg)}", flush=True)


def redact(text: str) -> str:
    for key in ("CIVITAI_TOKEN", "HF_TOKEN"):
        tok = os.environ.get(key)
        if tok:
            text = text.replace(tok, "***")
    return re.sub(r"(token=)[^&\s]+", r"\1***", text)


# ---------------------------------------------------------------------------
# 설정 로드
# ---------------------------------------------------------------------------
def load_config(path: Path | None = None) -> dict:
    """우선순위: MODELS_CONFIG_URL > 인자 경로 > /workspace/models.yaml > 이미지 기본값"""
    text = None
    url = os.environ.get("MODELS_CONFIG_URL", "").strip()
    if url:
        import requests
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        text = r.text
        log(f"원격 설정 사용: {url}")
    else:
        for cand in (path, WS / "models.yaml", APP / "config" / "models.yaml"):
            if cand and Path(cand).is_file():
                text = Path(cand).read_text(encoding="utf-8")
                log(f"설정 파일: {cand}")
                break
    cfg = (yaml.safe_load(text) if (text and yaml) else None) or {}
    cfg.setdefault("models", [])
    cfg.setdefault("custom_nodes", [])
    cfg.setdefault("workflows", [])
    cfg["models"] = [m for m in (cfg["models"] or []) if m and m.get("enabled", True)]
    cfg["models"] += parse_env_models(os.environ.get("MODELS", ""))
    return cfg


def parse_env_models(raw: str) -> list[dict]:
    """'civitai:123@loras https://x/y.safetensors@vae' → entry 목록"""
    out = []
    for tok in re.split(r"[\s,]+", raw.strip()):
        if not tok:
            continue
        src, folder = tok, ""
        if "@" in tok:
            a, b = tok.rsplit("@", 1)
            if a and re.fullmatch(r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*", b):
                src, folder = a, b
        entry: dict = {}
        if src.lower().startswith("civitai:"):
            entry["civitai"] = src.split(":", 1)[1]
        elif "civitai.com" in src:
            entry["civitai"] = src
        elif src.isdigit():
            entry["civitai"] = src
        else:
            entry["url"] = src
        if folder:
            entry["dir"] = folder
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Civitai
# ---------------------------------------------------------------------------
def parse_civitai_ref(ref) -> tuple[str, int]:
    """→ ('version', id) 또는 ('model', id)"""
    s = str(ref).strip()
    if s.isdigit():
        return "version", int(s)
    m = re.match(r"^(model|version)[:=](\d+)$", s, re.I)
    if m:
        return m.group(1).lower(), int(m.group(2))
    u = urlparse(s)
    q = parse_qs(u.query)
    if "modelVersionId" in q:
        return "version", int(q["modelVersionId"][0])
    m = re.search(r"/api/download/models/(\d+)", u.path) or re.search(r"/model-versions/(\d+)", u.path)
    if m:
        return "version", int(m.group(1))
    m = re.search(r"/models/(\d+)", u.path)
    if m:
        return "model", int(m.group(1))
    raise ValueError(f"Civitai 참조를 해석할 수 없습니다: {ref}")


def civitai_get(path: str) -> dict:
    import requests
    headers = {"User-Agent": "runpod-krea2-provisioner"}
    tok = os.environ.get("CIVITAI_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    for attempt in range(4):
        r = requests.get(f"{CIVITAI_API}{path}", headers=headers, timeout=30)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt * 2)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def pick_civitai_file(files: list[dict], name_filter: str | None = None) -> dict:
    cands = [f for f in files if f.get("type") in ("Model", "Pruned Model", None)] or list(files)
    if name_filter:
        nf = name_filter.lower()
        cands = [f for f in cands if nf in f.get("name", "").lower()]
        if not cands:
            raise ValueError(f"파일 필터 '{name_filter}' 에 맞는 파일이 없습니다: "
                             + ", ".join(f.get("name", "?") for f in files))
    if not cands:
        raise ValueError("다운로드할 파일이 없습니다")

    def score(f):
        meta = f.get("metadata") or {}
        return (
            1 if f.get("primary") else 0,
            1 if (meta.get("format") == "SafeTensor" or f.get("name", "").endswith(".safetensors")) else 0,
        )
    return sorted(cands, key=score, reverse=True)[0]


def resolve_civitai(entry: dict) -> dict:
    kind, ident = parse_civitai_ref(entry["civitai"])
    if kind == "model":
        model = civitai_get(f"/models/{ident}")
        versions = model.get("modelVersions") or []
        if not versions:
            raise ValueError(f"모델 {ident} 에 버전이 없습니다")
        vid = versions[0]["id"]
        log(f"Civitai 모델 {ident} → 최신 버전 {vid} ({versions[0].get('name')})")
    else:
        vid = ident
    ver = civitai_get(f"/model-versions/{vid}")
    f = pick_civitai_file(ver.get("files") or [], entry.get("file"))
    mtype = ((ver.get("model") or {}).get("type") or "").lower()
    folder = entry.get("dir") or CIVITAI_TYPE_DIR.get(mtype)
    if not folder:
        raise ValueError(f"Civitai type '{mtype}' 의 저장 폴더를 모릅니다. dir: 을 지정하세요")
    tok = os.environ.get("CIVITAI_TOKEN")
    url = f["downloadUrl"]
    if tok:
        url += ("&" if "?" in url else "?") + f"token={tok}"
    return {
        "key": f"civitai:{vid}:{entry.get('file') or ''}",
        "label": f"{(ver.get('model') or {}).get('name', '?')} / {ver.get('name', '?')} [{ver.get('baseModel', '?')}]",
        "url": url,
        "name": entry.get("name") or f["name"],
        "dir": folder,
        "size": int(float(f.get("sizeKB") or 0) * 1024),
        "kind": "civitai",
    }


# ---------------------------------------------------------------------------
# Hugging Face / 일반 URL
# ---------------------------------------------------------------------------
HF_RE = re.compile(r"^https?://huggingface\.co/([^/]+/[^/]+)/(?:blob|resolve)/([^/]+)/(.+?)(?:\?.*)?$")


def resolve_url(entry: dict) -> dict:
    url = entry["url"].strip()
    if not entry.get("dir"):
        raise ValueError(f"url 항목에는 dir: 이 필요합니다 → {url}")
    m = HF_RE.match(url)
    if m:
        repo, rev, fpath = m.groups()
        return {
            "key": f"hf:{repo}:{rev}:{fpath}",
            "label": f"HF {repo}/{fpath}",
            "url": f"https://huggingface.co/{repo}/resolve/{rev}/{fpath}",
            "hf": (repo, rev, fpath),
            "name": entry.get("name") or fpath.split("/")[-1],
            "dir": entry["dir"],
            "size": 0,
            "kind": "hf",
        }
    name = entry.get("name") or urlparse(url).path.rstrip("/").split("/")[-1]
    return {"key": f"url:{url}", "label": name, "url": url, "name": name,
            "dir": entry["dir"], "size": 0, "kind": "url"}


def resolve(entry: dict) -> dict:
    if "civitai" in entry:
        return resolve_civitai(entry)
    if "url" in entry:
        return resolve_url(entry)
    raise ValueError(f"알 수 없는 항목(civitai: 또는 url: 필요): {entry}")


# ---------------------------------------------------------------------------
# safetensors 검사
# ---------------------------------------------------------------------------
FULL_CKPT_PREFIXES = ("first_stage_model.", "cond_stage_model.", "conditioner.",
                      "text_encoders.", "vae.", "text_encoder.")


def safetensors_keys(path: Path) -> list[str] | None:
    try:
        with open(path, "rb") as fh:
            n = struct.unpack("<Q", fh.read(8))[0]
            if n <= 0 or n > 200 * 1024 * 1024:
                return None
            header = json.loads(fh.read(n))
        return [k for k in header if k != "__metadata__"]
    except Exception:
        return None


def is_full_checkpoint(path: Path) -> bool:
    keys = safetensors_keys(path) or []
    return any(k.startswith(FULL_CKPT_PREFIXES) for k in keys)


def auto_checkpoint_dir(path: Path) -> str:
    """Krea2 파인튜닝 '체크포인트'는 대개 DiT 가중치만 있음 → diffusion_models (UNETLoader)"""
    return "checkpoints" if is_full_checkpoint(path) else "diffusion_models"


# ---------------------------------------------------------------------------
# 다운로드
# ---------------------------------------------------------------------------
def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text())
    except Exception:
        return {}


_manifest_lock = threading.Lock()


def save_manifest_entry(key: str, path: Path) -> None:
    with _manifest_lock:
        m = load_manifest()
        m[key] = str(path)
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(m, indent=2, ensure_ascii=False))


def existing_target(item: dict) -> Path | None:
    dirs = ["checkpoints", "diffusion_models"] if item["dir"] == "@auto_checkpoint" else [item["dir"]]
    for d in dirs:
        p = MODELS_DIR / d / item["name"]
        if p.is_file() and p.stat().st_size > 0:
            if item.get("size") and abs(p.stat().st_size - item["size"]) > max(1024 * 1024, item["size"] * 0.01):
                log(f"크기가 다른 기존 파일 발견 → 다시 받습니다: {p}")
                return None
            return p
    return None


def _run_aria2(url: str, out_dir: Path, out_name: str) -> bool:
    if not shutil.which("aria2c"):
        return False
    cmd = ["aria2c", "-x", "16", "-s", "16", "-k", "1M", "--continue=true",
           "--file-allocation=none", "--auto-file-renaming=false", "--allow-overwrite=true",
           "--console-log-level=error", "--summary-interval=30", "--download-result=hide",
           "--max-tries=5", "--retry-wait=5", "-d", str(out_dir), "-o", out_name, url]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:  # type: ignore[union-attr]
        line = line.strip()
        if line:
            log(f"  aria2 {out_name}: {line[:200]}")
    return p.wait() == 0


def _run_requests(url: str, dest: Path) -> None:
    import requests
    with requests.get(url, stream=True, timeout=60, allow_redirects=True,
                      headers={"User-Agent": "runpod-krea2-provisioner"}) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                fh.write(chunk)


def _download_hf(item: dict, out_dir: Path) -> Path:
    from huggingface_hub import hf_hub_download
    repo, rev, fpath = item["hf"]
    p = hf_hub_download(repo_id=repo, filename=fpath, revision=rev,
                        local_dir=str(out_dir / "hf"), token=os.environ.get("HF_TOKEN") or None)
    return Path(p)


def download(item: dict) -> Path:
    found = existing_target(item)
    if found:
        log(f"이미 있음 → 건너뜀: {found.relative_to(MODELS_DIR)}")
        save_manifest_entry(item["key"], found)
        return found

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    part = TMP_DIR / (item["name"] + ".part")
    log(f"다운로드 시작: {item['label']} → {item['dir']}/{item['name']}"
        + (f" ({item['size'] / 1024 ** 3:.2f} GB)" if item.get("size") else ""))
    t0 = time.time()

    if item["kind"] == "hf":
        got = _download_hf(item, TMP_DIR)
        shutil.move(str(got), part)
    elif not _run_aria2(item["url"], TMP_DIR, part.name):
        log("  aria2 실패/없음 → requests 로 재시도")
        _run_requests(item["url"], part)

    if not part.is_file() or part.stat().st_size < 1024:
        raise RuntimeError("다운로드된 파일이 비어 있습니다 (토큰/권한 확인)")
    if part.name.endswith(".safetensors.part") and safetensors_keys(part) is None:
        head = part.read_bytes()[:200]
        part.unlink(missing_ok=True)
        raise RuntimeError(f"safetensors 형식이 아닙니다 (로그인/토큰 문제일 가능성). 응답 앞부분: {head!r}")

    folder = item["dir"]
    if folder == "@auto_checkpoint":
        folder = auto_checkpoint_dir(part)
        log(f"  체크포인트 구조 분석 → {folder}/ 에 배치"
            + (" (DiT 단독 가중치: 'Load Diffusion Model' 노드로 로드)" if folder == "diffusion_models" else ""))
    dest = MODELS_DIR / folder / item["name"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part, dest)
    for leftover in TMP_DIR.glob(item["name"] + ".part.aria2"):
        leftover.unlink(missing_ok=True)
    save_manifest_entry(item["key"], dest)
    log(f"완료: {dest.relative_to(MODELS_DIR)}  ({dest.stat().st_size / 1024 ** 3:.2f} GB, {time.time() - t0:.0f}s)")
    return dest


def provision_models(entries: list[dict]) -> int:
    if not entries:
        log("다운로드할 모델이 없습니다")
        return 0
    if any("civitai" in e for e in entries) and not os.environ.get("CIVITAI_TOKEN"):
        log("경고: CIVITAI_TOKEN 이 없습니다. 로그인이 필요한 Civitai 모델은 실패할 수 있습니다")

    manifest = load_manifest()
    failures = 0
    workers = max(1, int(os.environ.get("DOWNLOAD_CONCURRENCY", "3")))

    def job(entry):
        # 이전에 받은 Civitai 항목은 API 조회 없이 건너뜀
        if "civitai" in entry:
            try:
                kind, ident = parse_civitai_ref(entry["civitai"])
                key = f"civitai:{ident}:{entry.get('file') or ''}"
                if kind == "version" and key in manifest and Path(manifest[key]).is_file():
                    log(f"이미 있음 → 건너뜀: {Path(manifest[key]).relative_to(MODELS_DIR)}")
                    return
            except ValueError:
                pass
        download(resolve(entry))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(job, e): e for e in entries}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                failures += 1
                log(f"실패: {futs[fut]} → {exc}")

    log(f"모델 프로비저닝 끝: 총 {len(entries)}개, 실패 {failures}개. "
        "ComfyUI 화면에서 'R' 키(또는 새로고침)로 목록을 갱신하세요.")
    return failures


# ---------------------------------------------------------------------------
# 커스텀 노드 / 워크플로
# ---------------------------------------------------------------------------
def provision_nodes(urls: list) -> int:
    fails = 0
    nodes_dir = COMFY / "custom_nodes"
    for item in urls or []:
        url = item["url"] if isinstance(item, dict) else str(item)
        name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        dest = nodes_dir / name
        try:
            if not dest.exists():
                log(f"커스텀 노드 설치: {name}")
                subprocess.run(["git", "clone", "--depth", "1", url, str(dest)], check=True)
            req = dest / "requirements.txt"
            if req.is_file():
                subprocess.run(["uv", "pip", "install", "-q", "-r", str(req)], check=True)
        except Exception as exc:  # noqa: BLE001
            fails += 1
            log(f"커스텀 노드 실패 {url}: {exc}")
    return fails


def provision_workflows(urls: list) -> int:
    wf_dir = WS / "user" / "default" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    bundled = APP / "workflows"
    if bundled.is_dir():
        for f in bundled.glob("*.json"):
            if not (wf_dir / f.name).exists():
                shutil.copy(f, wf_dir / f.name)
                log(f"워크플로 추가: {f.name}")
    fails = 0
    for url in urls or []:
        name = urlparse(url).path.split("/")[-1]
        if (wf_dir / name).exists():
            continue
        try:
            _run_requests(url, wf_dir / name)
            log(f"워크플로 추가: {name}")
        except Exception as exc:  # noqa: BLE001
            fails += 1
            log(f"워크플로 실패 {url}: {exc}")
    return fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--only", default="nodes,workflows,models",
                    help="쉼표 구분: nodes, workflows, models")
    args = ap.parse_args(argv)
    parts = {p.strip() for p in args.only.split(",")}

    cfg = load_config(args.config)
    fails = 0
    if "nodes" in parts:
        fails += provision_nodes(cfg["custom_nodes"])
    if "workflows" in parts:
        fails += provision_workflows(cfg["workflows"])
    if "models" in parts:
        fails += provision_models(cfg["models"])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
