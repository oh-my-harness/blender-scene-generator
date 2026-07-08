"""Blender addon: TCP server that receives JSON commands and executes bpy operations.

All bpy operations are scheduled on Blender's main thread via a timer queue,
because bpy.context and bpy.ops are not safe to call from background threads.
"""

import bpy
import json
import socket
import threading
import queue as _queue

HOST = "127.0.0.1"
PORT = 9876

# Queue of (callable, result_queue) pairs. The timer drains this on the main thread.
_main_thread_queue: _queue.Queue = _queue.Queue()


def _ok(data=None):
    return {"ok": True, "data": data if data is not None else {}}


def _err(msg):
    return {"ok": False, "error": str(msg)}


def _run_on_main(fn, timeout: float = 30.0):
    """Schedule fn on Blender's main thread and wait for the result."""
    result_q: _queue.Queue = _queue.Queue()
    _main_thread_queue.put((fn, result_q))
    try:
        return result_q.get(timeout=timeout)  # blocks until the timer runs fn
    except _queue.Empty:
        return _err(f"timeout: Blender main thread did not execute within {timeout}s")


def _timer_drain():
    """Timer callback (runs on main thread). Drains the queue."""
    while True:
        try:
            fn, result_q = _main_thread_queue.get_nowait()
        except _queue.Empty:
            break
        try:
            result_q.put(fn())
        except Exception as e:
            result_q.put(_err(e))
    # Return 0.0 to keep the timer recurring (Blender re-runs it ASAP).
    # Returning None would cancel the timer.
    return 0.0


def add_object(params):
    obj_type = params["type"]
    name = params.get("name", obj_type.capitalize())
    location = params.get("location", [0, 0, 0])
    scale = params.get("scale", [1, 1, 1])
    rotation = params.get("rotation", [0, 0, 0])

    def _do():
        if obj_type == "cube":
            bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation, scale=scale)
        elif obj_type == "sphere":
            bpy.ops.mesh.primitive_uv_sphere_add(location=location, rotation=rotation, scale=scale)
        elif obj_type == "cylinder":
            bpy.ops.mesh.primitive_cylinder_add(location=location, rotation=rotation, scale=scale)
        elif obj_type == "plane":
            bpy.ops.mesh.primitive_plane_add(location=location, rotation=rotation, scale=scale)
        elif obj_type == "cone":
            bpy.ops.mesh.primitive_cone_add(location=location, rotation=rotation, scale=scale)
        elif obj_type == "torus":
            bpy.ops.mesh.primitive_torus_add(location=location, rotation=rotation, scale=scale)
        else:
            return _err(f"unknown object type: {obj_type}")

        obj = bpy.context.active_object
        obj.name = name
        return _ok({"name": obj.name})

    return _run_on_main(_do)


def set_material(params):
    target = params["target"]
    color = params.get("color", [0.8, 0.8, 0.8])
    # Blender's Base Color is a 4D (RGBA) vector. Accept 3- or 4-element
    # input and pad to 4 with alpha = 1.0 so the BSDF input is always valid.
    if len(color) == 3:
        color = [color[0], color[1], color[2], 1.0]
    roughness = params.get("roughness", 0.5)
    metallic = params.get("metallic", 0.0)

    def _do():
        obj = bpy.data.objects.get(target)
        if obj is None:
            return _err(f"object not found: {target}")
        # Reuse an existing material on the object if present; otherwise create one.
        # Avoids material pile-up on repeated adjustments.
        mat = obj.active_material
        if mat is None:
            mat = bpy.data.materials.new(name=f"{target}_mat")
            mat.use_nodes = True
            obj.data.materials.append(mat)
        elif not mat.use_nodes:
            mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = color
            bsdf.inputs["Roughness"].default_value = roughness
            bsdf.inputs["Metallic"].default_value = metallic
        return _ok({"target": target})

    return _run_on_main(_do)


def add_light(params):
    light_type = params["type"]
    name = params.get("name", "Light")
    location = params.get("location", [0, 0, 5])
    energy = params.get("energy", 100)
    color = params.get("color", [1.0, 1.0, 1.0])

    def _do():
        light_data = bpy.data.lights.new(name=name, type=light_type.upper())
        light_data.energy = energy
        light_data.color = color
        light_obj = bpy.data.objects.new(name, light_data)
        bpy.context.collection.objects.link(light_obj)
        light_obj.location = location
        return _ok({"name": name})

    return _run_on_main(_do)


def set_camera(params):
    location = params.get("location", [7, -7, 5])
    rotation = params.get("rotation", [1.1, 0, 0.785])

    def _do():
        cam_obj = bpy.data.objects.get("Camera")
        if cam_obj is None or cam_obj.type != "CAMERA":
            cam_data = bpy.data.cameras.new("Camera")
            cam_obj = bpy.data.objects.new("Camera", cam_data)
            bpy.context.collection.objects.link(cam_obj)
        cam_obj.location = location
        cam_obj.rotation_euler = rotation
        bpy.context.scene.camera = cam_obj
        return _ok({"name": "Camera"})

    return _run_on_main(_do)


def get_scene_state(params):
    def _do():
        objects = []
        for obj in bpy.data.objects:
            entry = {
                "name": obj.name,
                "type": obj.type,
                "location": list(obj.location),
                "scale": list(obj.scale),
                "rotation": [round(a, 4) for a in obj.rotation_euler],
            }
            # Material slots (names + base color) so the reviewer can detect
            # missing materials without a second round-trip.
            if obj.type == "MESH":
                mats = []
                for slot in obj.material_slots:
                    mat = slot.material
                    if mat is None:
                        continue
                    bsdf = (
                        mat.node_tree.nodes.get("Principled BSDF")
                        if mat.use_nodes and mat.node_tree
                        else None
                    )
                    if bsdf is not None:
                        color = list(bsdf.inputs["Base Color"].default_value)
                        mats.append({
                            "name": mat.name,
                            "color": [round(c, 4) for c in color[:3]],
                            "roughness": round(float(bsdf.inputs["Roughness"].default_value), 4),
                            "metallic": round(float(bsdf.inputs["Metallic"].default_value), 4),
                        })
                    else:
                        mats.append({"name": mat.name})
                entry["materials"] = mats
            # Light-specific fields so the reviewer can judge brightness.
            if obj.type == "LIGHT" and obj.data is not None:
                light = obj.data
                entry["light_type"] = light.type
                entry["energy"] = round(float(light.energy), 4)
                entry["color"] = [round(c, 4) for c in light.color]
            objects.append(entry)
        return _ok({"objects": objects})

    return _run_on_main(_do)

def delete_object(params):
    name = params["name"]

    def _do():
        obj = bpy.data.objects.get(name)
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
            return _ok({"deleted": name})
        return _err(f"object not found: {name}")

    return _run_on_main(_do)


def update_object(params):
    name = params["name"]
    location = params.get("location")
    scale = params.get("scale")
    rotation = params.get("rotation")

    def _do():
        obj = bpy.data.objects.get(name)
        if obj is None:
            return _err(f"object not found: {name}")
        if location:
            obj.location = location
        if scale:
            obj.scale = scale
        if rotation:
            obj.rotation_euler = rotation
        return _ok({"name": name})

    return _run_on_main(_do)


def execute_python(params):
    code = params["code"]

    def _do():
        exec(code, {"bpy": bpy})
        return _ok({})

    return _run_on_main(_do)

def boolean_modify(params):
    """Apply a boolean operation between a target object and a cutter primitive.

    The cutter is a transient primitive created from `cutter` spec, applied
    as union/difference/intersect to `target`, then deleted. The target's
    mesh is modified in place.
    """
    target_name = params["target"]
    operation = params["operation"]  # union | difference | intersect
    cutter_spec = params["cutter"]   # {type, location, scale, rotation, ...}

    def _do():
        target = bpy.data.objects.get(target_name)
        if target is None:
            return _err(f"object not found: {target_name}")
        if target.type != "MESH":
            return _err(f"boolean target must be a mesh, got {target.type}")

        # Build the cutter primitive in-place.
        ctype = cutter_spec.get("type", "cube")
        cloc = cutter_spec.get("location", [0, 0, 0])
        cscale = cutter_spec.get("scale", [1, 1, 1])
        crot = cutter_spec.get("rotation", [0, 0, 0])

        if ctype == "cube":
            bpy.ops.mesh.primitive_cube_add(location=cloc, rotation=crot, scale=cscale)
        elif ctype == "sphere":
            bpy.ops.mesh.primitive_uv_sphere_add(location=cloc, rotation=crot, scale=cscale)
        elif ctype == "cylinder":
            bpy.ops.mesh.primitive_cylinder_add(location=cloc, rotation=crot, scale=cscale)
        elif ctype == "plane":
            bpy.ops.mesh.primitive_plane_add(location=cloc, rotation=crot, scale=cscale)
        elif ctype == "cone":
            bpy.ops.mesh.primitive_cone_add(location=cloc, rotation=crot, scale=cscale)
        elif ctype == "torus":
            bpy.ops.mesh.primitive_torus_add(location=cloc, rotation=crot, scale=cscale)
        else:
            return _err(f"unknown cutter type: {ctype}")
        cutter = bpy.context.active_object
        cutter.name = f"_bool_cutter_{target_name}"

        # Apply the boolean modifier to the target.
        mod = target.modifiers.new(name="Boolean", type="BOOLEAN")
        mod.operation = operation.upper()
        mod.object = cutter
        # Use the exact solver for reliability; fast solver has artifacts.
        mod.solver = "EXACT"

        # Make the cutter active so we can apply the modifier on the target.
        bpy.context.view_layer.objects.active = target
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception as e:
            target.modifiers.remove(mod)
            bpy.data.objects.remove(cutter, do_unlink=True)
            return _err(f"boolean apply failed: {e}")

        # Remove the cutter object — it has served its purpose.
        bpy.data.objects.remove(cutter, do_unlink=True)
        return _ok({"target": target_name, "operation": operation})

    return _run_on_main(_do)


def extrude_shape(params):
    """Extrude a 2D profile polygon along an axis to create a prismatic mesh.

    `profile` is a list of [x, y] points (closed polygon). `depth` is the
    extrusion length. `axis` controls which axis the depth extends along.
    """
    name = params.get("name", "ExtrudedShape")
    profile = params["profile"]  # [[x,y], ...]
    depth = params.get("depth", 1.0)
    axis = params.get("axis", "Z")  # X | Y | Z
    location = params.get("location", [0, 0, 0])
    rotation = params.get("rotation", [0, 0, 0])

    def _do():
        if len(profile) < 3:
            return _err("profile must have at least 3 points")

        # Build vertices: bottom ring + top ring.
        verts = []
        axis_idx = {"X": 0, "Y": 1, "Z": 2}.get(axis.upper(), 2)
        for p in profile:
            v = [0.0, 0.0, 0.0]
            v[0] = p[0]
            v[1] = p[1]
            v[2] = 0.0
            verts.append(v)
        for p in profile:
            v = [0.0, 0.0, 0.0]
            v[0] = p[0]
            v[1] = p[1]
            v[axis_idx] = depth
            verts.append(v)

        n = len(profile)
        faces = []
        # Bottom face (reversed for correct normal).
        bottom = list(range(n))
        bottom.reverse()
        faces.append(bottom)
        # Top face.
        faces.append(list(range(n, 2 * n)))
        # Side quads.
        for i in range(n):
            j = (i + 1) % n
            faces.append([i, j, j + n, i + n])

        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = rotation
        return _ok({"name": obj.name})

    return _run_on_main(_do)


def add_curve(params):
    """Create a bezier curve with bevel depth (tube/pipe shape).

    `points` is a list of [x,y,z] control points. The curve is a poly spline
    connecting them, with `bevel_depth` giving the tube radius.
    """
    name = params.get("name", "Curve")
    points = params["points"]  # [[x,y,z], ...]
    bevel_depth = params.get("bevel_depth", 0.1)
    location = params.get("location", [0, 0, 0])
    rotation = params.get("rotation", [0, 0, 0])

    def _do():
        if len(points) < 2:
            return _err("curve needs at least 2 points")
        curve_data = bpy.data.curves.new(name, type="CURVE")
        curve_data.dimensions = "3D"
        curve_data.bevel_depth = bevel_depth
        spline = curve_data.splines.new(type="POLY")
        spline.points.add(len(points) - 1)
        for i, p in enumerate(points):
            spline.points[i].co = (p[0], p[1], p[2], 1.0)
        obj = bpy.data.objects.new(name, curve_data)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = rotation
        return _ok({"name": obj.name})

    return _run_on_main(_do)


def render(params):
    output_path = params.get("output_path", "/tmp/blender_render.png")

    def _do():
        bpy.context.scene.render.engine = "BLENDER_EEVEE"
        bpy.context.scene.render.resolution_x = 1920
        bpy.context.scene.render.resolution_y = 1080
        bpy.context.scene.render.filepath = output_path
        bpy.ops.render.render(write_still=True)
        return _ok({"image_path": output_path})

    return _run_on_main(_do)


def viewport_refresh(params):
    """Force-refresh all viewports so the UI reflects recent changes."""

    def _do():
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        region.tag_redraw()
        return _ok({})

    return _run_on_main(_do)

ACTIONS = {
    "add_object": add_object,
    "set_material": set_material,
    "add_light": add_light,
    "set_camera": set_camera,
    "get_scene_state": get_scene_state,
    "viewport_refresh": viewport_refresh,
    "delete_object": delete_object,
    "update_object": update_object,
    "execute_python": execute_python,
    "boolean_modify": boolean_modify,
    "extrude_shape": extrude_shape,
    "add_curve": add_curve,
    "render": render,
}

def handle_command(req):
    action = req.get("action")
    params = req.get("params", {})
    fn = ACTIONS.get(action)
    if fn is None:
        return _err(f"unknown action: {action}")
    try:
        return fn(params)
    except Exception as e:
        return _err(e)


def recv_exactly(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def handle_client(conn):
    try:
        while True:
            # 4-byte big-endian length prefix
            len_data = recv_exactly(conn, 4)
            if len_data is None:
                break
            msg_len = int.from_bytes(len_data, "big")
            if msg_len == 0 or msg_len > 100 * 1024 * 1024:
                break
            body = recv_exactly(conn, msg_len)
            if body is None:
                break
            req = json.loads(body.decode("utf-8"))
            resp = handle_command(req)
            resp_bytes = json.dumps(resp).encode("utf-8")
            conn.sendall(len(resp_bytes).to_bytes(4, "big") + resp_bytes)
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        conn.close()


def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)
    print(f"[addon] listening on {HOST}:{PORT}")
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()


# Entry point when loaded via --python
# Run the TCP server in a daemon thread so Blender's GUI main loop can proceed.
# Register a timer that drains the command queue on Blender's main thread.
bpy.app.timers.register(_timer_drain, persistent=True)
threading.Thread(target=start_server, daemon=True).start()
print(f"[addon] server thread started, listening on {HOST}:{PORT}")
