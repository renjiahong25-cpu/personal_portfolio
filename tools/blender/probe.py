import bpy
import sys

print("BLENDER_VERSION", bpy.app.version_string)
print("PYTHON", sys.version.split()[0])

try:
    import io

    import PIL
    from PIL import Image

    im = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    buf = io.BytesIO()
    im.save(buf, format="WEBP")
    print("BLENDER_PIL", PIL.__version__, "WEBP_OK", len(buf.getvalue()))
except Exception as e:
    print("BLENDER_PIL_UNAVAILABLE", repr(e))

try:
    engines = [e.bl_idname for e in bpy.types.RenderEngine.__subclasses__()]
    print("RENDER_ENGINES_SUBCLASSES", engines)
except Exception as e:
    print("RENDER_ENGINES_SUBCLASSES_FAILED", repr(e))

try:
    ids = [it.identifier for it in bpy.types.Scene.render_engine.items]
    print("RENDER_ENGINES_RNA", ids)
except Exception as e:
    print("RENDER_ENGINES_RNA_FAILED", repr(e))

scene = bpy.context.scene
for cand in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES", "WORKBENCH"):
    try:
        scene.render.engine = cand
        print("ENGINE_ASSIGN_OK", cand)
    except Exception as e:
        print("ENGINE_ASSIGN_FAIL", cand, repr(e))
