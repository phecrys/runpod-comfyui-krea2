"""workflows/krea2_lora_t2i.json 생성기 (UI 형식)"""
import json, sys
from pathlib import Path

nodes, links = [], []
lid = [0]
def link(src, sslot, dst, dslot, typ):
    lid[0] += 1
    links.append([lid[0], src["id"], sslot, dst["id"], dslot, typ])
    src["outputs"][sslot]["links"].append(lid[0])
    dst["inputs"][dslot]["link"] = lid[0]

def node(i, typ, pos, size, inputs, outputs, widgets, title=None, mode=0, color=None):
    n = {"id": i, "type": typ, "pos": pos, "size": size, "flags": {}, "order": i, "mode": mode,
         "inputs": [{"name": a, "type": b, "link": None} for a, b in inputs],
         "outputs": [{"name": a, "type": b, "links": [], "slot_index": k} for k, (a, b) in enumerate(outputs)],
         "properties": {"Node name for S&R": typ}, "widgets_values": widgets}
    if title: n["title"] = title
    if color: n["color"], n["bgcolor"] = color
    nodes.append(n); return n

unet = node(1, "UNETLoader", [40, 80], [340, 82], [], [("MODEL", "MODEL")],
            ["krea2_turbo_fp8_scaled.safetensors", "default"], "Krea2 모델 (Civitai 체크포인트도 여기)")
lora1 = node(2, "LoraLoaderModelOnly", [420, 80], [320, 82], [("model", "MODEL")], [("MODEL", "MODEL")],
             ["krea2_retroanime.safetensors", 1.0], "LoRA 1 (Ctrl+B 로 켜기)", mode=4)
lora2 = node(3, "LoraLoaderModelOnly", [780, 80], [320, 82], [("model", "MODEL")], [("MODEL", "MODEL")],
             ["krea2_softwatercolor.safetensors", 1.0], "LoRA 2 (Ctrl+B 로 켜기)", mode=4)
clip = node(4, "CLIPLoader", [40, 240], [340, 106], [], [("CLIP", "CLIP")],
            ["qwen3vl_4b_fp8_scaled.safetensors", "krea2", "default"], "텍스트 인코더 (type=krea2)")
vae = node(5, "VAELoader", [40, 400], [340, 58], [], [("VAE", "VAE")], ["qwen_image_vae.safetensors"])
pos = node(6, "CLIPTextEncode", [420, 240], [420, 200], [("clip", "CLIP")], [("CONDITIONING", "CONDITIONING")],
           ["a cinematic photo of a red fox standing in a snowy birch forest at golden hour, soft rim light, 35mm film"],
           "프롬프트", color=("#232", "#353"))
neg = node(7, "CLIPTextEncode", [420, 480], [420, 90], [("clip", "CLIP")], [("CONDITIONING", "CONDITIONING")],
           [""], "네거티브 (CFG 1.0 이면 영향 없음)", color=("#322", "#533"))
lat = node(8, "EmptySD3LatentImage", [420, 610], [320, 106], [], [("LATENT", "LATENT")], [1024, 1024, 1], "해상도")
ks = node(9, "KSampler", [880, 240], [320, 262],
          [("model", "MODEL"), ("positive", "CONDITIONING"), ("negative", "CONDITIONING"), ("latent_image", "LATENT")],
          [("LATENT", "LATENT")], [0, "randomize", 8, 1.0, "euler", "simple", 1.0])
dec = node(10, "VAEDecode", [1240, 240], [210, 46], [("samples", "LATENT"), ("vae", "VAE")], [("IMAGE", "IMAGE")], [])
save = node(11, "SaveImage", [1240, 330], [420, 460], [("images", "IMAGE")], [], ["Krea2/img"])
note = node(12, "Note", [1140, -150], [520, 330], [], [], [
"Krea2 + Civitai LoRA 워크플로\n\n"
"• Turbo 기본값: 8 steps / CFG 1.0 / euler / simple\n"
"• RAW(비증류) 계열 체크포인트: 30~50 steps, CFG 3~3.5 로 올리세요\n"
"• Civitai 체크포인트는 첫 번째 'Krea2 모델' 노드에서 선택\n"
"• LoRA 노드는 기본 '우회(보라색)' 상태 → 선택 후 Ctrl+B 로 켜기\n"
"• 새로 받은 모델이 안 보이면 키보드 R (새로고침)\n"
"• 'krea2' type 이 없다는 오류 → ComfyUI 가 구버전 (이미지 재빌드)\n"
"• 검은 이미지가 나오면 텍스트 인코더를 bf16 버전으로 교체해 보세요\n"
"• 정확한 공식 설정은 Template Library 의 'Krea-2 Turbo' 와 비교"])

link(unet, 0, lora1, 0, "MODEL"); link(lora1, 0, lora2, 0, "MODEL"); link(lora2, 0, ks, 0, "MODEL")
link(clip, 0, pos, 0, "CLIP"); link(clip, 0, neg, 0, "CLIP")
link(pos, 0, ks, 1, "CONDITIONING"); link(neg, 0, ks, 2, "CONDITIONING"); link(lat, 0, ks, 3, "LATENT")
link(ks, 0, dec, 0, "LATENT"); link(vae, 0, dec, 1, "VAE"); link(dec, 0, save, 0, "IMAGE")

wf = {"last_node_id": 12, "last_link_id": lid[0], "nodes": nodes, "links": links, "groups": [],
      "config": {}, "extra": {"ds": {"scale": 0.8, "offset": [60, 220]}}, "version": 0.4}
Path(sys.argv[1]).write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
print("ok", len(nodes), "nodes", len(links), "links")
