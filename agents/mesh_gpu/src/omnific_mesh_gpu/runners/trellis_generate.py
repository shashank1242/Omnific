"""Runs INSIDE the trellis2 conda env (not our venv): image -> textured GLB with microsoft/TRELLIS.2.

Follows the upstream example.py (TRELLIS.2 README): Trellis2ImageTo3DPipeline + o_voxel.postprocess.to_glb.
Usage: python trellis_generate.py --image in.png --out out.glb [--seed 0] [--decimation-target N] [--texture-size N]
"""

import argparse
import os

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")  # upstream: saves GPU memory

import o_voxel
import torch
from PIL import Image
from trellis2.pipelines import Trellis2ImageTo3DPipeline


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--decimation-target", type=int, default=50_000)
    ap.add_argument("--texture-size", type=int, default=1024)
    ap.add_argument("--model", default=os.environ.get("MESH_GEN_MODEL", "microsoft/TRELLIS.2-4B"))
    a = ap.parse_args()

    torch.manual_seed(a.seed)
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(a.model)
    pipeline.cuda()
    mesh = pipeline.run(Image.open(a.image))[0]
    mesh.simplify(16777216)  # nvdiffrast limit (upstream example)
    glb = o_voxel.postprocess.to_glb(
        vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs, coords=mesh.coords,
        attr_layout=mesh.layout, voxel_size=mesh.voxel_size, aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=a.decimation_target, texture_size=a.texture_size,
        remesh=True, remesh_band=1, remesh_project=0, verbose=True,
    )
    glb.export(a.out)  # plain PNG/JPEG textures: our loader and three.js read them without KHR_texture_webp


if __name__ == "__main__":
    main()
