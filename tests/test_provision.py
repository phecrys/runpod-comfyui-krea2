import json, os, struct, sys, tempfile, importlib
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp())
os.environ["WORKSPACE_DIR"] = str(tmp / "ws")
os.environ["COMFYUI_DIR"] = str(tmp / "comfy")
os.environ["CIVITAI_TOKEN"] = "SECRET123"
sys.path.insert(0, str(ROOT / "scripts"))
import provision as P

def st(path, keys):
    hdr = {k: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]} for k in keys}
    hdr["__metadata__"] = {}
    b = json.dumps(hdr).encode()
    path.write_bytes(struct.pack("<Q", len(b)) + b + b"\0" * 4096)

# refs
assert P.parse_civitai_ref(123) == ("version", 123)
assert P.parse_civitai_ref("https://civitai.com/models/111?modelVersionId=222") == ("version", 222)
assert P.parse_civitai_ref("https://civitai.com/models/111/some-name") == ("model", 111)
assert P.parse_civitai_ref("https://civitai.com/api/download/models/333?type=Model") == ("version", 333)
assert P.parse_civitai_ref("model:5") == ("model", 5)
# env models
e = P.parse_env_models("civitai:123@loras https://huggingface.co/a/b/blob/main/x/y.safetensors@vae, 999 https://civitai.com/models/1?modelVersionId=2@loras/krea2")
assert e == [{"civitai": "123", "dir": "loras"},
             {"url": "https://huggingface.co/a/b/blob/main/x/y.safetensors", "dir": "vae"},
             {"civitai": "999"},
             {"civitai": "https://civitai.com/models/1?modelVersionId=2", "dir": "loras/krea2"}], e
# HF url
r = P.resolve_url({"url": "https://huggingface.co/Comfy-Org/Krea-2/blob/main/vae/qwen_image_vae.safetensors", "dir": "vae"})
assert r["hf"] == ("Comfy-Org/Krea-2", "main", "vae/qwen_image_vae.safetensors") and r["name"] == "qwen_image_vae.safetensors"
# file pick
files = [{"name": "a.pt", "type": "Model", "metadata": {"format": "PickleTensor"}},
         {"name": "b_fp8.safetensors", "type": "Model", "primary": True, "metadata": {"format": "SafeTensor"}},
         {"name": "b_bf16.safetensors", "type": "Model", "metadata": {"format": "SafeTensor"}},
         {"name": "train.zip", "type": "Training Data"}]
assert P.pick_civitai_file(files)["name"] == "b_fp8.safetensors"
assert P.pick_civitai_file(files, "bf16")["name"] == "b_bf16.safetensors"
# civitai resolve with mocked API
def fake_get(path):
    if path == "/models/111": return {"modelVersions": [{"id": 777, "name": "v3"}]}
    if path == "/model-versions/777": return {"name": "v3", "baseModel": "Krea 2", "model": {"name": "MyKrea", "type": "Checkpoint"},
        "files": [{"name": "mykrea.safetensors", "type": "Model", "primary": True, "sizeKB": 1000,
                   "downloadUrl": "https://civitai.com/api/download/models/777"}]}
    if path == "/model-versions/888": return {"name": "v1", "model": {"name": "Sty", "type": "LORA"},
        "files": [{"name": "sty.safetensors", "type": "Model", "downloadUrl": "https://civitai.com/api/download/models/888?type=Model&format=SafeTensor"}]}
    raise AssertionError(path)
P.civitai_get = fake_get
it = P.resolve({"civitai": "https://civitai.com/models/111"})
assert it["dir"] == "@auto_checkpoint" and it["url"].endswith("?token=SECRET123") and it["key"] == "civitai:777:"
it2 = P.resolve({"civitai": 888})
assert it2["dir"] == "loras" and it2["url"].endswith("&token=SECRET123")
assert "SECRET123" not in P.redact("url " + it2["url"])
# auto checkpoint
d = tmp / "x"; d.mkdir()
st(d / "dit.safetensors", ["model.diffusion_model.blocks.0.w", "img_in.weight"])
st(d / "full.safetensors", ["model.diffusion_model.a", "first_stage_model.decoder.w", "conditioner.x"])
assert P.auto_checkpoint_dir(d / "dit.safetensors") == "diffusion_models"
assert P.auto_checkpoint_dir(d / "full.safetensors") == "checkpoints"
(d / "bad.safetensors").write_bytes(b"<html>login</html>" * 100)
assert P.safetensors_keys(d / "bad.safetensors") is None

# end-to-end download with fake transport
src = tmp / "srcfile.safetensors"; st(src, ["img_in.weight"])
def fake_aria(url, out_dir, out_name):
    import shutil; shutil.copy(src, out_dir / out_name); return True
P._run_aria2 = fake_aria
it["size"] = src.stat().st_size
dest = P.download(it)
assert dest == P.MODELS_DIR / "diffusion_models" / "mykrea.safetensors", dest
assert json.loads(P.MANIFEST.read_text())["civitai:777:"] == str(dest)
# second time skipped
assert P.download(it) == dest
# provision_models skip-by-manifest + failure counting
calls = []
P.resolve = lambda e: (calls.append(e), (_ for _ in ()).throw(RuntimeError("boom")))[1]
f = P.provision_models([{"civitai": 777}, {"url": "https://x/y.safetensors", "dir": "vae"}])
assert f == 1 and len(calls) == 1, (f, calls)   # 777 skipped via manifest, url failed
# config
importlib.reload(P)
cfg = P.load_config(ROOT / "config" / "models.yaml")
assert len(cfg["models"]) == 3 and cfg["custom_nodes"] is None
print("ALL TESTS PASSED")
