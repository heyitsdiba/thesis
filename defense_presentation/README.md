# Defense presentation: 3D HEK293T cell

Work in progress for the thesis defense deck: a 3D cutaway of a HEK293T producer cell
that the talk zooms into, region by region, to explain the pathways from the meta-analysis.

## Layout

| Path | What it is |
|---|---|
| `scripts/build_cell.py` | Builds the cell in Blender (via the `bpy` Python module), renders stills, exports `.glb`/`.blend` |
| `scripts/make_test_deck.py` | Builds a test `.pptx`: option B (rendered image + Morph zoom) and option A (native 3D model + Morph) |
| `renders/` | Rendered images |
| `models/hek293t_cell.glb` | 3D model for PowerPoint (Insert → 3D Models → This Device) |
| `models/hek293t_cell.blend` | Blender scene, if you want to open it in Blender yourself |
| `deck/` | Generated PowerPoint files |
| `viewer/hek293t_viewer.html` | Browser viewer for the `.glb` (published as a private artifact) |

## Regenerate

```bash
pip install bpy==4.2.0 python-pptx
cd defense_presentation
python3 scripts/build_cell.py --samples 96 --res 1920x1080 \
    --out renders/hek293t_cell_overview.png --blend models/hek293t_cell.blend
python3 scripts/build_cell.py --glb models/hek293t_cell.glb
python3 scripts/make_test_deck.py renders/hek293t_cell_overview.png models/hek293t_cell.glb deck/test_options_A_B.pptx
base64 -w0 models/hek293t_cell.glb > viewer/hek293t_cell.glb.txt   # viewer data, not committed
```

Units in the model are roughly micrometres (cell about 17 × 13 × 5 µm). The layout is seeded,
so re-running gives the same cell.
