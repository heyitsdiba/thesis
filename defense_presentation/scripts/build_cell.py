"""Build a stylised 3D HEK293T cell (cutaway) in Blender and render / export it.

Usage:
    python3 build_cell.py [--samples N] [--res WxH] [--out PATH] [--glb PATH] [--blend PATH]

Units are roughly micrometres. The cell is adherent and flattened (HEK293T-like),
with a quarter-wedge cutaway exposing the nucleus and cytoplasm.
"""
import argparse
import math
import random
import sys

import bpy  # noqa: I001  (bpy must be imported before bmesh)
import bmesh
from mathutils import Vector, noise

SEED = 293
random.seed(SEED)

# ---------------------------------------------------------------- geometry ---
CELL_RX, CELL_RY, CELL_H = 8.5, 6.5, 5.0
NUC_C = Vector((-0.4, 0.4, 2.35))
NUC_R = Vector((3.6, 3.0, 1.85))
FLOOR = 0.3  # cutaway keeps the cell floor below this height
WEDGE_X, WEDGE_Y = -0.3, 0.3  # cutaway removes x > WEDGE_X and y < WEDGE_Y


def outline(theta):
    """Irregular radial modulation of the cell outline."""
    return 1 + 0.10 * math.sin(3 * theta + 0.4) + 0.06 * math.sin(5 * theta + 1.3) + 0.04 * math.sin(8 * theta)


def top_height(x, y):
    th = math.atan2(y, x)
    k = outline(th)
    v = 1 - (x / (CELL_RX * k)) ** 2 - (y / (CELL_RY * k)) ** 2
    return CELL_H * math.sqrt(v) if v > 0 else 0.0


def in_wedge(p, margin=0.0):
    return p.x > WEDGE_X - margin and p.y < WEDGE_Y + margin and p.z > FLOOR - margin


def in_nucleus(p, pad=0.0):
    d = p - NUC_C
    return (d.x / (NUC_R.x + pad)) ** 2 + (d.y / (NUC_R.y + pad)) ** 2 + (d.z / (NUC_R.z + pad)) ** 2 < 1


# --------------------------------------------------------------- materials ---
def mat(name, color, rough=0.45, sss=0.0, coat=0.15, emission=None, alpha=1.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    b.inputs["Coat Roughness"].default_value = 0.3
    if sss:
        b.inputs["Subsurface Weight"].default_value = sss
        b.inputs["Subsurface Radius"].default_value = (0.4, 0.3, 0.3)
        b.inputs["Subsurface Scale"].default_value = 0.15
    if emission:
        b.inputs["Emission Color"].default_value = (*emission[0], 1)
        b.inputs["Emission Strength"].default_value = emission[1]
    if alpha < 1:
        b.inputs["Alpha"].default_value = alpha
    return m


def srgb(h):
    """Hex -> linear RGB tuple."""
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


M = {}


def make_materials():
    M["membrane"] = mat("Membrane", srgb("#9ec5ea"), rough=0.35, sss=0.25, coat=0.3)
    M["cut"] = mat("CutFace", srgb("#f4d58d"), rough=0.6, coat=0.0)
    M["nuc_env"] = mat("NuclearEnvelope", srgb("#b59ad6"), rough=0.4, sss=0.2, coat=0.25)
    M["nuc_cut"] = mat("NucleusCut", srgb("#e9c46a"), rough=0.6, coat=0.0)
    M["nucleoplasm"] = mat("Nucleoplasm", srgb("#e7defa"), rough=0.7, coat=0.0)
    M["nucleolus"] = mat("Nucleolus", srgb("#6d4aa8"), rough=0.4, sss=0.2)
    M["chromatin"] = mat("Chromatin", srgb("#9b7bd0"), rough=0.5)
    M["pore"] = mat("NuclearPore", srgb("#4f3a86"), rough=0.4)
    M["er"] = mat("ER", srgb("#7fd1c7"), rough=0.4, sss=0.2, coat=0.2)
    M["golgi"] = mat("Golgi", srgb("#f28fb1"), rough=0.35, sss=0.2, coat=0.25)
    M["mito"] = mat("Mitochondria", srgb("#f29e6d"), rough=0.35, sss=0.25, coat=0.3)
    M["lyso"] = mat("Lysosome", srgb("#8fcf6f"), rough=0.35, sss=0.2, coat=0.3)
    M["endo"] = mat("Endosome", srgb("#6fa8dc"), rough=0.35, sss=0.2, coat=0.3)
    M["vesicle"] = mat("Vesicle", srgb("#ffd36e"), rough=0.3, coat=0.3)
    M["mt"] = mat("Microtubule", srgb("#7cc47f"), rough=0.5)
    M["actin"] = mat("Actin", srgb("#e9a3a3"), rough=0.5)
    M["centrosome"] = mat("Centrosome", srgb("#3d7dbf"), rough=0.4)
    M["ground"] = mat("Ground", (0.95, 0.955, 0.965), rough=0.9, coat=0.0)


# ----------------------------------------------------------------- helpers ---
def link(obj, coll=None):
    (coll or bpy.context.scene.collection).objects.link(obj)
    return obj


def new_coll(name):
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c


def mesh_obj(name, bm, material, coll, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        me.shade_smooth()
    me.materials.append(material)
    return link(bpy.data.objects.new(name, me), coll)


def uv_sphere_bm(r=1.0, seg=48, rings=24):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, radius=r)
    return bm


def ico_bm(r=1.0, sub=3):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=sub, radius=r)
    return bm


def add_subsurf(obj, levels=1, render=2):
    m = obj.modifiers.new("Subsurf", "SUBSURF")
    m.levels, m.render_levels = levels, render
    return m


def add_solidify(obj, thickness, rim_mat_index=None):
    m = obj.modifiers.new("Solidify", "SOLIDIFY")
    m.thickness = thickness
    m.offset = -1
    if rim_mat_index is not None:
        m.material_offset_rim = rim_mat_index
    return m


def cutter():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    big = 40
    for v in bm.verts:
        v.co.x = WEDGE_X + (v.co.x + 0.5) * big
        v.co.y = WEDGE_Y + (v.co.y - 0.5) * big
        v.co.z = FLOOR + (v.co.z + 0.5) * big
    me = bpy.data.meshes.new("Cutter")
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new("Cutter", me)
    link(o)
    o.hide_render = True
    o.hide_viewport = True
    return o


def wedge_cut(bm):
    """Slice the mesh along the wedge planes and delete the part inside, leaving a clean open edge."""
    for co, no in (((WEDGE_X, 0, 0), (1, 0, 0)), ((0, WEDGE_Y, 0), (0, 1, 0)), ((0, 0, FLOOR), (0, 0, 1))):
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, plane_co=co, plane_no=no)
    kill = [f for f in bm.faces if in_wedge(f.calc_center_median())]
    bmesh.ops.delete(bm, geom=kill, context="FACES")


# ---------------------------------------------------------------- building ---
def build_membrane(coll, cutter_obj):
    bm = uv_sphere_bm(1.0, seg=128, rings=64)
    for v in bm.verts:
        x, y, z = v.co
        th = math.atan2(y, x)
        k = outline(th)
        # flattened adherent profile: dome on top, almost flat underside
        zz = z * CELL_H if z > 0 else z * 0.35
        p = Vector((x * CELL_RX * k, y * CELL_RY * k, zz))
        bump = noise.noise(p * 0.45) * 0.18 + noise.noise(p * 1.6) * 0.05
        p += Vector((x, y, max(z, 0))).normalized() * bump
        v.co = p
    # sit on the ground plane
    minz = min(v.co.z for v in bm.verts)
    for v in bm.verts:
        v.co.z -= minz
    wedge_cut(bm)
    o = mesh_obj("PlasmaMembrane", bm, M["membrane"], coll)
    o.data.materials.append(M["cut"])
    add_solidify(o, 0.22, rim_mat_index=1)
    return o


def build_filopodia(coll):
    for i in range(14):
        th = random.uniform(0, 2 * math.pi)
        k = outline(th)
        base = Vector((math.cos(th) * CELL_RX * k * 0.93, math.sin(th) * CELL_RY * k * 0.93, 0.12))
        length = random.uniform(1.2, 2.6)
        d = Vector((math.cos(th), math.sin(th), 0.0))
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.16, radius2=0.04, depth=length)
        o = mesh_obj(f"Filopodium.{i:02d}", bm, M["membrane"], coll)
        o.location = base + d * length / 2
        o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()


def build_nucleus(coll, cutter_obj):
    bm = uv_sphere_bm(1.0, seg=96, rings=48)
    for v in bm.verts:
        p = Vector((v.co.x * NUC_R.x, v.co.y * NUC_R.y, v.co.z * NUC_R.z))
        p += v.co.normalized() * noise.noise(p * 0.6 + Vector((5, 5, 5))) * 0.12
        v.co = p + NUC_C
    wedge_cut(bm)
    o = mesh_obj("NuclearEnvelope", bm, M["nuc_env"], coll)
    o.data.materials.append(M["nuc_cut"])
    add_solidify(o, 0.16, rim_mat_index=1)

    # nucleoplasm floor: a flattened disc filling the cut so the interior reads as a volume
    bm = uv_sphere_bm(1.0, seg=96, rings=48)
    for v in bm.verts:
        v.co = Vector((v.co.x * (NUC_R.x - 0.2), v.co.y * (NUC_R.y - 0.2), v.co.z * (NUC_R.z - 0.2))) + NUC_C
    inner = mesh_obj("Nucleoplasm", bm, M["nucleoplasm"], coll)
    # only keep the lower part so the inside stays visible: cut with a horizontal box
    hb = bmesh.new()
    bmesh.ops.create_cube(hb, size=1)
    for v in hb.verts:
        v.co = Vector((v.co.x * 30, v.co.y * 30, (v.co.z + 0.5) * 20 + NUC_C.z - 0.9))
    hme = bpy.data.meshes.new("NucCutH")
    hb.to_mesh(hme)
    hb.free()
    hcut = link(bpy.data.objects.new("NucCutH", hme))
    hcut.hide_render = hcut.hide_viewport = True
    m = inner.modifiers.new("Lower", "BOOLEAN")
    m.operation, m.object, m.solver = "DIFFERENCE", hcut, "FAST"

    # nucleoli
    for i, (off, r) in enumerate([((0.9, -0.6, 0.0), 0.95), ((-0.6, 0.9, 0.2), 0.7), ((1.5, -1.6, -0.2), 0.5)]):
        bm = ico_bm(1.0, 4)
        for v in bm.verts:
            v.co *= r * (1 + noise.noise(v.co * 2.2 + Vector((i, 0, 0))) * 0.18)
        nl = mesh_obj(f"Nucleolus.{i}", bm, M["nucleolus"], coll)
        nl.location = NUC_C + Vector(off) + Vector((0, 0, -0.55))
        nl.scale = (1, 1, 0.8)

    # chromatin: wiggly tubes inside the nucleus
    for i in range(16):
        cd = bpy.data.curves.new(f"Chromatin.{i:02d}", "CURVE")
        cd.dimensions = "3D"
        cd.bevel_depth = 0.07
        cd.bevel_resolution = 3
        sp = cd.splines.new("NURBS")
        n = 14
        sp.points.add(n - 1)
        p = NUC_C + Vector((random.uniform(-2.6, 2.6), random.uniform(-2.0, 2.0), random.uniform(-1.2, -0.2)))
        for j in range(n):
            p = p + Vector((random.uniform(-0.45, 0.45), random.uniform(-0.45, 0.45), random.uniform(-0.2, 0.2)))
            d = p - NUC_C
            if (d.x / (NUC_R.x - 0.4)) ** 2 + (d.y / (NUC_R.y - 0.4)) ** 2 + (d.z / (NUC_R.z - 0.4)) ** 2 > 1:
                p = NUC_C + d * 0.85
            sp.points[j].co = (*p, 1)
        sp.use_endpoint_u = True
        sp.order_u = 4
        cd.materials.append(M["chromatin"])
        link(bpy.data.objects.new(f"Chromatin.{i:02d}", cd), coll)

    # nuclear pores on the envelope (skipping the removed wedge)
    placed = 0
    tries = 0
    while placed < 70 and tries < 5000:
        tries += 1
        u, w = random.uniform(0, 2 * math.pi), math.acos(random.uniform(-0.6, 1))
        n = Vector((math.sin(w) * math.cos(u), math.sin(w) * math.sin(u), math.cos(w)))
        p = NUC_C + Vector((n.x * NUC_R.x, n.y * NUC_R.y, n.z * NUC_R.z)) * 1.0
        if in_wedge(p, 0.3):
            continue
        normal = Vector((n.x / NUC_R.x, n.y / NUC_R.y, n.z / NUC_R.z)).normalized()
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=False, segments=16, radius1=0.2, radius2=0.2, depth=0.12)
        o = mesh_obj(f"NuclearPore.{placed:02d}", bm, M["pore"], coll)
        o.location = p + normal * 0.04
        o.rotation_euler = normal.to_track_quat("Z", "Y").to_euler()
        tor = o.modifiers.new("Thick", "SOLIDIFY")
        tor.thickness = 0.08
        placed += 1


def build_er(coll, cutter_obj):
    """Fenestrated ER sheets wrapped around the nucleus."""
    for layer, scale in enumerate((1.18, 1.36, 1.55)):
        bm = uv_sphere_bm(1.0, seg=96, rings=48)
        for v in bm.verts:
            p = Vector((v.co.x * NUC_R.x * scale, v.co.y * NUC_R.y * scale, v.co.z * NUC_R.z * (1 + (scale - 1) * 0.6)))
            p += v.co.normalized() * noise.noise(p * 0.8 + Vector((layer * 3, 0, 0))) * 0.25
            v.co = p + NUC_C
        bm.faces.ensure_lookup_table()
        kill = []
        for f in bm.faces:
            c = f.calc_center_median()
            nval = noise.noise(c * 0.9 + Vector((0, layer * 7.0, 0)))
            out_of_cell = c.z < 0.5 or c.z > top_height(c.x, c.y) - 0.35
            if nval < -0.05 or out_of_cell or in_wedge(c) or (c.z - NUC_C.z) > NUC_R.z * 0.9:
                kill.append(f)
        bmesh.ops.delete(bm, geom=kill, context="FACES")
        o = mesh_obj(f"ER.{layer}", bm, M["er"], coll)
        add_solidify(o, 0.12)
        add_subsurf(o, 1, 2)  # rounds off the fenestrations


def build_golgi(coll):
    """Stack of curved cisternae, concave side toward the nucleus, plus budding vesicles."""
    center = NUC_C + Vector((3.9, -3.0, -0.95))  # inside the open wedge, next to the nucleus
    facing = (NUC_C - center)
    facing.z = 0
    facing.normalize()
    rot = facing.to_track_quat("Z", "Y")
    for i in range(6):
        long_r = 1.55 - abs(i - 2.5) * 0.17
        bm = uv_sphere_bm(1.0, 48, 16)
        for v in bm.verts:
            x, y, z = v.co.x * long_r, v.co.y * 0.62, v.co.z * 0.085
            # dilated rims, cupped toward the nucleus
            z *= 1 + 1.4 * (v.co.x ** 6)
            z += 0.32 * (x / 1.55) ** 2 * 1.6
            v.co = Vector((x, y, z))
        o = mesh_obj(f"Golgi.{i}", bm, M["golgi"], coll)
        o.rotation_mode = "QUATERNION"
        o.rotation_quaternion = rot
        o.location = center + facing * (i * 0.27)
    for i in range(12):
        bm = ico_bm(random.uniform(0.11, 0.18), 2)
        o = mesh_obj(f"GolgiVesicle.{i:02d}", bm, M["golgi"], coll)
        side = random.choice((-1, 1))
        local = Vector((side * random.uniform(1.4, 1.9), random.uniform(-0.5, 0.5), random.uniform(-0.3, 1.6)))
        o.location = center + rot @ local


def random_cyto_point(margin=0.6, open_bias=0.0, floor_band=False):
    for _ in range(10000):
        p = Vector((random.uniform(-CELL_RX, CELL_RX), random.uniform(-CELL_RY, CELL_RY), 0))
        if open_bias and random.random() < open_bias:
            p.x, p.y = abs(p.x), -abs(p.y)
        if (p.x / CELL_RX) ** 2 + (p.y / CELL_RY) ** 2 > 0.62:
            continue
        h = top_height(p.x, p.y)
        if h < 2.2:
            continue
        p.z = random.uniform(0.6, max(0.7, h - margin - 0.4))
        if floor_band and in_wedge(p):
            p.z = random.uniform(0.55, 1.1)  # keep the opening readable: organelles rest low
            if in_nucleus(p, 1.2):
                continue
        if in_nucleus(p, 0.8) or in_nucleus(p + Vector((0, 0, 0.4)), 0.8):
            continue
        return p
    raise RuntimeError("no point")


def build_mitochondria(coll, n=24):
    for i in range(n):
        p = random_cyto_point(open_bias=0.25, floor_band=True)
        bm = uv_sphere_bm(1.0, 32, 16)
        length = random.uniform(0.6, 1.1)
        for v in bm.verts:
            v.co = Vector((v.co.x * length, v.co.y * 0.24, v.co.z * 0.24))
        o = mesh_obj(f"Mitochondrion.{i:02d}", bm, M["mito"], coll)
        bend = o.modifiers.new("Bend", "SIMPLE_DEFORM")
        bend.deform_method = "BEND"
        bend.angle = math.radians(random.uniform(-70, 70))
        bend.deform_axis = "Z"
        o.location = p
        o.rotation_euler = (random.uniform(-0.3, 0.3), random.uniform(-0.3, 0.3), random.uniform(0, math.pi))


def build_spheres(coll, name, material, n, rmin, rmax, bias=0.5):
    for i in range(n):
        p = random_cyto_point(open_bias=bias, floor_band=True)
        bm = ico_bm(1.0, 3)
        r = random.uniform(rmin, rmax)
        for v in bm.verts:
            v.co *= r * (1 + noise.noise(v.co * 2 + Vector((i, 0, 0))) * 0.1)
        o = mesh_obj(f"{name}.{i:02d}", bm, material, coll)
        o.location = p


def build_cytoskeleton(coll):
    centro = NUC_C + Vector((3.4, -2.0, -0.6))
    for i in range(2):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.12, radius2=0.12, depth=0.45)
        o = mesh_obj(f"Centriole.{i}", bm, M["centrosome"], coll)
        o.location = centro + Vector((0.15 * i, 0, 0))
        o.rotation_euler = (math.pi / 2 * i, 0, 0)
    # microtubules radiate from the centrosome toward the periphery
    for i in range(22):
        cd = bpy.data.curves.new(f"Microtubule.{i:02d}", "CURVE")
        cd.dimensions = "3D"
        cd.bevel_depth = 0.028
        cd.bevel_resolution = 2
        sp = cd.splines.new("NURBS")
        th = random.uniform(0, 2 * math.pi)
        k = outline(th)
        end = Vector((math.cos(th) * CELL_RX * k * 0.85, math.sin(th) * CELL_RY * k * 0.85, 0))
        end.z = random.uniform(0.5, 1.0)
        pts = []
        for j in range(8):
            t = j / 7
            p = centro.lerp(end, t)
            # arc around the nucleus rather than through it
            for _ in range(20):
                if in_nucleus(p, 0.4):
                    d = p - NUC_C
                    d.z = 0
                    p += d.normalized() * 0.4
            p += Vector((random.uniform(-0.25, 0.25), random.uniform(-0.25, 0.25), random.uniform(-0.1, 0.1))) * (t > 0)
            p.z = min(max(p.z, 0.5), max(0.55, top_height(p.x, p.y) - 0.4), NUC_C.z - 0.3)
            pts.append(p)
        sp.points.add(len(pts) - 1)
        for j, p in enumerate(pts):
            sp.points[j].co = (*p, 1)
        sp.use_endpoint_u = True
        sp.order_u = 4
        cd.materials.append(M["mt"])
        link(bpy.data.objects.new(f"Microtubule.{i:02d}", cd), coll)
    # cortical actin: short fibres just under the membrane, near the floor
    for i in range(40):
        th = random.uniform(0, 2 * math.pi)
        k = outline(th)
        rr = random.uniform(0.55, 0.8)
        a = Vector((math.cos(th) * CELL_RX * k * rr, math.sin(th) * CELL_RY * k * rr, random.uniform(0.4, 0.6)))
        d = Vector((random.uniform(-1, 1), random.uniform(-1, 1), 0)).normalized() * random.uniform(0.6, 1.2)
        cd = bpy.data.curves.new(f"Actin.{i:02d}", "CURVE")
        cd.dimensions = "3D"
        cd.bevel_depth = 0.02
        sp = cd.splines.new("POLY")
        sp.points.add(1)
        sp.points[0].co = (*a, 1)
        sp.points[1].co = (*(a + d), 1)
        cd.materials.append(M["actin"])
        link(bpy.data.objects.new(f"Actin.{i:02d}", cd), coll)


# ------------------------------------------------------------ scene setup ---
def setup_scene(samples, res):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    try:
        sc.cycles.denoiser = "OPENIMAGEDENOISE"
    except Exception:
        pass
    sc.cycles.max_bounces = 6
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Base Contrast"
    sc.view_settings.exposure = 0.35

    world = bpy.data.worlds.new("World")
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.92, 0.94, 0.97, 1)
    bg.inputs["Strength"].default_value = 0.9

    # ground plane catches soft shadows
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=80)
    g = mesh_obj("Ground", bm, M["ground"], sc.collection, smooth=False)
    g.location.z = -0.01

    def area(name, loc, size, energy, color=(1, 1, 1)):
        ld = bpy.data.lights.new(name, "AREA")
        ld.size = size
        ld.energy = energy
        ld.color = color
        lo = link(bpy.data.objects.new(name, ld))
        lo.location = loc
        lo.rotation_euler = (Vector((0, 0, 1.5)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()

    area("Key", (14, -16, 22), 14, 9000)
    area("Fill", (-20, -8, 12), 18, 3000, (0.9, 0.95, 1.0))
    area("Rim", (-6, 22, 14), 12, 3500, (1.0, 0.95, 0.9))
    area("Top", (0, 0, 30), 20, 2500)

    cam_d = bpy.data.cameras.new("Camera")
    cam_d.lens = 50
    cam = link(bpy.data.objects.new("Camera", cam_d))
    cam.location = (17.0, -19.5, 23.0)
    target = Vector((0.6, -0.8, 1.0))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam
    cam_d.dof.use_dof = True
    cam_d.dof.focus_distance = (target - cam.location).length
    cam_d.dof.aperture_fstop = 8.0


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=64)
    ap.add_argument("--res", default="1920x1080")
    ap.add_argument("--out", default=None)
    ap.add_argument("--glb", default=None)
    ap.add_argument("--blend", default=None)
    a = ap.parse_args(argv)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    make_materials()

    cell = new_coll("Cell")
    nucleus = new_coll("Nucleus")
    organelles = new_coll("Organelles")
    cytoskeleton = new_coll("Cytoskeleton")

    cut_obj = None
    build_membrane(cell, cut_obj)
    build_filopodia(cell)
    build_nucleus(nucleus, cut_obj)
    build_er(organelles, cut_obj)
    build_golgi(organelles)
    build_mitochondria(organelles)
    build_spheres(organelles, "Lysosome", M["lyso"], 9, 0.28, 0.4, bias=0.3)
    build_spheres(organelles, "Endosome", M["endo"], 10, 0.22, 0.35, bias=0.3)
    build_spheres(organelles, "Vesicle", M["vesicle"], 30, 0.1, 0.16, bias=0.3)
    build_cytoskeleton(cytoskeleton)

    w, h = (int(x) for x in a.res.split("x"))
    setup_scene(a.samples, (w, h))

    if a.blend:
        bpy.ops.wm.save_as_mainfile(filepath=a.blend)
    if a.glb:
        # glTF for PowerPoint's native 3D model support: bake modifiers, skip helpers
        # glTF has no curves: turn the cytoskeleton/chromatin tubes into meshes first
        bpy.ops.object.select_all(action="DESELECT")
        curves = [o for o in bpy.data.objects if o.type == "CURVE"]
        for o in curves:
            o.select_set(True)
        if curves:
            bpy.context.view_layer.objects.active = curves[0]
            bpy.ops.object.convert(target="MESH")
        for o in bpy.data.objects:
            o.select_set(o.type == "MESH" and not o.hide_render and o.name != "Ground")
        bpy.ops.export_scene.gltf(filepath=a.glb, export_format="GLB", use_selection=True,
                                  export_apply=True, export_cameras=False, export_lights=False)
    if a.out:
        bpy.context.scene.render.filepath = a.out
        bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
