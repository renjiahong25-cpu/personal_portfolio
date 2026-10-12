import bpy

mat = bpy.data.materials.new("probe_mat")
mat.use_nodes = True
bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
print("BSDF_INPUTS", [(s.name, s.identifier) for s in bsdf.inputs])
print("MAT_IDS", [p.identifier for p in bpy.types.Material.bl_rna.properties])
print("HAS_EEVEE", hasattr(mat, "eevee"))
if hasattr(mat, "eevee"):
    print("MAT_EEVEE", [p.identifier for p in mat.eevee.bl_rna.properties])
for prop in ("blend_method", "show_transparent_back", "render_method", "surface_render_method"):
    print("HAS", prop, hasattr(mat, prop))
print("OPA_HAS", hasattr(bpy.ops.mesh, "primitive_torus_add"),
      hasattr(bpy.ops.mesh, "primitive_ico_sphere_add"),
      hasattr(bpy.ops.mesh, "primitive_uv_sphere_add"))
