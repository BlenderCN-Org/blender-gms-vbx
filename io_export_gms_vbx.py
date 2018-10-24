bl_info = {
    "name": "Export GM:Studio BLMod",
    "description": "Exporter for GameMaker:Studio with customizable vertex format",
    "author": "Bart Teunis",
    "version": (0, 7, 3),
    "blender": (2, 79, 0),
    "location": "File > Export",
    "warning": "", # used for warning icon and text in addons panel
    "wiki_url": "https://github.com/bartteunis/blender-gms-vbx/wiki",
    "category": "Import-Export"}

# Required imports
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty, CollectionProperty
from bpy.types import Object, Operator, PropertyGroup
import bpy
import shutil                       # for image file copy
import json
from os import path, makedirs
from os.path import splitext, split
from struct import pack, calcsize

### Export Function Definitions ###
def fetch_attribs(desc,node,ba,byte_pos,frame):
    """"Fetch the attribute values from the given node and place in ba at byte_pos"""
    id = node.bl_rna.identifier
    if id in desc:
        for prop, occurences in desc[id].items():                   # Property name and occurences in bytedata
            for offset, attr_blen, fmt, index, func in occurences:  # Each occurence's data (tuple assignment!)
                ind = byte_pos+offset
                ba[frame-index][ind:ind+attr_blen] = pack(fmt,*getattr(node,prop))

def write_object_ba(obj,desc,ba,frame):
    """Traverse the object's mesh data at the given frame and write to the appropriate bytearray in ba using the description data structure provided"""
    desc, vertex_format_bytesize = desc
    
    # TODO: write scene attributes here
    
    
    mod_tri = obj.modifiers.new('triangulate_for_export','TRIANGULATE')
    m = obj.to_mesh(bpy.context.scene,True,'RENDER')
    obj.modifiers.remove(mod_tri)
    m.transform(obj.matrix_world)
    
    # Loop through triangles
    ba_pos = 0
    for p in m.polygons:
        # Loop through vertices
        for li in p.loop_indices:
            loop = m.loops[li]
            fetch_attribs(desc,loop,ba,ba_pos,frame)
            
            vertex = m.vertices[loop.vertex_index]
            fetch_attribs(desc,vertex,ba,ba_pos,frame)
            
            # We wrote a full vertex, so we can now increment the bytearray position by the vertex format size
            ba_pos += vertex_format_bytesize
    
    bpy.data.meshes.remove(m)

def construct_ds(obj,attr):
    """Constructs the data structure required to move through the attributes of a given object"""
    desc, offset = {}, 0
    
    for a in attr:
        ident, atn, format, fo, func = a
        
        if ident not in desc:
            desc[ident] = {}
        dct_obj = desc[ident]
        
        if atn not in dct_obj:
            dct_obj[atn] = []
        lst_attr = dct_obj[atn]
        
        prop_rna = getattr(bpy.types,ident).bl_rna.properties[atn]
        attrib_bytesize = calcsize(format)
        
        lst_attr.append((offset,attrib_bytesize,format,fo,func))
        offset += attrib_bytesize
        
    return (desc, offset)

def construct_ba(obj,desc,frame_count):
    """Construct the required bytearrays to store vertex data for the given object for the given number of frames"""
    mod_tri = obj.modifiers.new('triangulate_for_export','TRIANGULATE')
    m = obj.to_mesh(bpy.context.scene,True,'RENDER')
    obj.modifiers.remove(mod_tri)
    no_verts = len(m.polygons) * 3
    bpy.data.meshes.remove(m)                                   # TODO: any easier way to get number of vertices??
    desc, vertex_format_bytesize = desc
    ba = [bytearray([0] * no_verts * vertex_format_bytesize) for i in range(0,frame_count)]
    return ba

### End of Export Function Definitions ###

# Conversion functions (go into the globals() dictionary for now...)
def float_to_byte(val):
    """Convert value in range [0,1] to an integer value in range [0,255]"""
    return int(val*255)

def vec_to_bytes(val):
    """Convert a list of values in range [0,1] to a list of integer values in range [0,255]"""
    return [int(x*255) for x in val]

def invert_v(val):
    """Invert the v coordinate of a (u,v) pair"""
    return [val[0],1-val[1]]

def invert_y(val):
    """Invert the y coordinate of a vector"""
    return [val[0],-val[1],val[2]]

def vertex_group_ids_to_bitmask(vertex):
    """Return a bitmask containing the vertex groups a vertex belongs to"""
    list = [x.group for x in vertex.groups]
    masked = 0
    for group in list:
        masked |= 1 << group
    return masked

# Stuff to export physics
def object_physics_to_json(obj):
    """For objects of type 'MESH', exports all edge loops that make up a face or polygon. Each one becomes a chain fixture in Game Maker."""
    physics_props = {'angular_damping','collision_shape','enabled','friction','kinematic','linear_damping','mass','restitution','type'}
    b = obj.rigid_body
    
    if b == None:
        return {}
    
    physics_settings = {x:b.path_resolve(x) for x in physics_props}
    physics_settings['collision_group'] = [i for i, x in enumerate(b.collision_groups) if x == True][0]
    
    # Get reference to object data
    d = obj.data
    
    # Select the necessary stuff
    physics_settings['coords'] = []
    for poly in d.polygons:
        vtx_indices = [d.loops[x].vertex_index for x in poly.loop_indices]
        ordered_verts = [d.vertices[x].co.xy[:] for x in vtx_indices]
        physics_settings['coords'].append(ordered_verts)
    
    return physics_settings

def object_get_texture_name(obj):
    """Returns the name of the texture image if the object has one defined"""
    tex_name = ""
    for ms in obj.material_slots:
        mat = ms.material
        if mat != None:
            ts = mat.texture_slots[0]
            if (ts != None):
                tex = ts.texture
                tex_name = tex.image.name
    return tex_name

def object_get_diffuse_color(obj):
    if (len(obj.material_slots) > 0):
        return obj.material_slots[0].material.diffuse_color[:]
    else:
        return (1.0,1.0,1.0)

# Custom type to be used in collection
class AttributeType(bpy.types.PropertyGroup):
    # Getter and setter functions
    def test_cb(self,context):
        props = getattr(bpy.types,self.type).bl_rna.properties
        items = [(p.identifier,p.name,p.description) for p in props]
        return items
    
    #def update_type(self, context):
    #    self.attr = test_cb(self,context)
    
    def set_format_from_type(self, context):
        attr = getattr(bpy.types,self.type).bl_rna.properties[self.attr]
        map_fmt = {'FLOAT':'f','INT':'i', 'BOOLEAN':'?'}    # TODO: extend this list a bit more
        type = map_fmt.get(attr.type,'*')                   # Asterisk '*' means "I don't know what this should be..."
        self.fmt = type * attr.array_length if attr.is_array else type
    
    # Currently supported attribute sources, maintained manually at the moment
    supported_sources = {'MeshVertex','MeshLoop','MeshUVLoop','ShapeKeyPoint','VertexGroupElement','Material','MeshLoopColor','MeshPolygon','Scene','Object'}
    source_items = []
    for src in supported_sources:
        id = getattr(bpy.types,src)
        rna = id.bl_rna
        source_items.append((rna.identifier,rna.name,rna.description))
    
    # Actual properties
    type = bpy.props.EnumProperty(name="Source", description="Where to get the data from", items=source_items, default="MeshVertex")
    attr = bpy.props.EnumProperty(name="Attribute", description="Which attribute to get", items=test_cb, update = set_format_from_type)
    fmt = bpy.props.StringProperty(name="Format", description="The format string to be used for the binary data", default="fff")
    int = bpy.props.IntProperty(name="Int", description="Interpolation offset, i.e. 0 means value at current frame, 1 means value at next frame", default=0, min=0, max=1)
    func = bpy.props.StringProperty(name="Function", description="'Pre-processing' function to be called before conversion to binary format - must exist in globals()", default="")
    #func = bpy.props.EnumProperty(name="Function", description="'Pre-processing' function to be called before conversion to binary format - must exist in globals()", items=[("","",""),("float_to_byte","float_to_byte",""),("vec_to_bytes","vec_to_bytes",""),("invert_v","invert_v",""),("invert_y","invert_y",""),("vertex_group_ids_to_bitmask","vertex_group_ids_to_bitmask","")], default="")

# Operators to get the vertex format customization add/remove to work
# See https://blender.stackexchange.com/questions/57545/can-i-make-a-ui-button-that-makes-buttons-in-a-panel
class AddAttributeOperator(Operator):
    """Add a new attribute to the vertex format"""
    bl_idname = "export_scene.add_attribute_operator"
    bl_label = "Add Vertex Attribute"

    def execute(self, context):
        # context.active_operator refers to ExportGMSVertexBuffer instance
        context.active_operator.vertex_format.add()
        return {'FINISHED'}

class RemoveAttributeOperator(Operator):
    """Remove the selected attribute from the vertex format"""
    bl_idname = "export_scene.remove_attribute_operator"
    bl_label = "Remove Vertex Attribute"
    
    id = bpy.props.IntProperty()
    
    def execute(self, context):
        # context.active_operator refers to ExportGMSVertexBuffer instance
        context.active_operator.vertex_format.remove(self.id)
        return {'FINISHED'}

# Register these here already
bpy.utils.register_class(AttributeType)
bpy.utils.register_class(AddAttributeOperator)
bpy.utils.register_class(RemoveAttributeOperator)

# ExportHelper is a helper class, defines filename and
# invoke() function which calls the file selector.
class ExportGMSVertexBuffer(Operator, ExportHelper):
    """Export (parts of) the current scene to a vertex buffer, including textures and a description file in JSON format"""
    bl_idname = "export_scene.gms_blmod" # important since its how bpy.ops.export_scene.gms_blmod is constructed
    bl_label = "Export GM:Studio BLMod"
    bl_options = {'PRESET'}                 # Allow presets of exporter configurations
    
    def __init__(self):
        # Blender Python trickery: dynamic addition of an index variable to the class
        bpy.types.Object.batch_index = bpy.props.IntProperty(name="Batch Index")    # Each instance now has a batch index!
        for i, obj in enumerate([obj for obj in bpy.context.selected_objects if obj.type == 'MESH']):
            obj.batch_index = i

    # ExportHelper mixin class uses this
    filename_ext = ".json"

    filter_glob = StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )
    
    ### Property Definitions ###
    selection_only = BoolProperty(
        name="Selection Only",
        default=True,
        description="Only export objects that are currently selected",
    )
    
    reverse_loop = BoolProperty(
        name="Reverse Loop",
        default=False,
        description="Reverse looping through triangle indices",
    )
    
    frame_option = EnumProperty(
        name="Frame",
        description="Which frames to export",
        items=(('cur',"Current","Export current frame only"),
               ('all',"All","Export all frames in range"),
        )
    )
    
    batch_mode = EnumProperty(
        name="Batch Mode",
        description="How to split individual object data over files",
        items=(('one',"Single File", "Batch all into a single file"),
               ('perobj',"Per Object", "Create a file for each object in the selection"),
               ('perfra',"Per Frame", "Create a file for each frame"),
               ('objfra',"Per Object Then Frame", "Create a directory for each object with a file for each frame"),
               ('fraobj',"Per Frame Then Object", "Create a directory for each frame with a file for each object"),
        )
    )
    
    handedness = EnumProperty(
        name="Handedness",
        description="Handedness of the coordinate system to be used",
        items=(('rh',"Right handed",""),
               ('lh',"Left handed",""),
        )
    )
    
    export_mesh_data = BoolProperty(
        name="Export Mesh Data",
        default=False,
        description="Whether to export mesh data to a separate, binary file (.vbx)",
    )
    
    vertex_format = CollectionProperty(
        name="Vertex Format",
        type=bpy.types.AttributeType,
    )
    
    join_into_active = BoolProperty(
        name="Join Into Active",
        default=False,
        description="Whether to join the selection into the active object",
    )
    
    split_by_material = BoolProperty(
        name="Split By Material",
        default=False,
        description="Whether to split joined mesh by material after joining",
    )
    
    export_textures = BoolProperty(
        name="Export Textures",
        default=True,
        description="Export texture images to same directory as result file",
    )
    ### End of Property Definitions ###
    
    def draw(self, context):
        layout = self.layout
        
        box = layout.box()
        
        box.label("General:")
        
        box.prop(self,'selection_only')
        box.prop(self,'frame_option')
        box.prop(self,'batch_mode')
        
        box = layout.box()
        
        box.label("Mesh Data:")
        box.prop(self,"export_mesh_data")
        
        if self.export_mesh_data == True:
            box.label("Vertex Format:")
            
            box.operator("export_scene.add_attribute_operator",text="Add")
            
            for index, item in enumerate(self.vertex_format):
                row = box.row()
                row.prop(item,'type')
                row.prop(item,'attr')
                row.prop(item,'fmt')
                row.prop(item,'func')
                row.prop(item,'int')
                opt_remove = row.operator("export_scene.remove_attribute_operator",text="Remove")
                opt_remove.id = index
        
        box = layout.box()
        
        box.label("Transforms:")
        
        box.prop(self,'handedness')
        box.prop(self,'reverse_loop')
        
        box = layout.box()
        
        box.label("Extras:")
        
        box.prop(self,'join_into_active')
        box.prop(self,'split_by_material')
        box.prop(self,'export_textures')

    def execute(self, context):
        # Prepare a bit
        root, ext = splitext(self.filepath)
        base, fname = split(self.filepath)
        
        # Join step
        if self.join_into_active:
            bpy.ops.object.join()
        
        # TODO: transformation and axes step
        
        
        # Get objects
        s = context.scene
        mesh_selection = [obj for obj in context.selected_objects if obj.type == 'MESH']
        
        # Attribs
        attribs = [(i.type,i.attr,i.fmt,i.int,i.func) for i in self.vertex_format]
        print(attribs)
        
        # Split by material
        if self.split_by_material:
            bpy.ops.mesh.separate(type='MATERIAL')
        
        # << Prepare a structure to map vertex attributes to the actual contents >>
        frame_count = s.frame_end-s.frame_start+1 if self.frame_option == 'all' else 1
        
        ba_per_object = {}
        desc_per_object = {}
        for obj in mesh_selection:
            desc_per_object[obj] = construct_ds(obj,attribs)
            print(desc_per_object[obj])
            ba_per_object[obj] = construct_ba(obj,desc_per_object[obj],frame_count)
        
        # << End of preparation of structure >>
        
        # << Now execute >>
        
        # Loop through scene frames
        for i in range(frame_count):
            # First set the current frame
            bpy.context.scene.frame_set(s.frame_start+i)
            
            # Now add frame vertex data for the current object
            for obj in mesh_selection:
                write_object_ba(obj,desc_per_object[obj],ba_per_object[obj],i)
        
        # Final step: write all bytearrays to one or more file(s) in one or more directories
        f = open(root + ".vbx","wb")
        
        for obj, ba in ba_per_object.items():
            for b in ba:
                f.write(b)
        
        f.close()
        
        # Create JSON file (very basic at the moment...)
        # Coming up next: 
        # for type_collection in [bpy.data.meshes,bpy.data.objects,bpy.data.materials,bpy.data.images,bpy.data.textures,bpy.data.cameras,bpy.data.lamps]:
        #    [{i:getattr(ins,i) for i in ins.bl_rna.properties.keys()} for ins in type_collection]
        desc = {}
        desc["objects"]   = [{
                            "name":obj.name,
                            "type":obj.type,
                            "file":path.basename(self.filepath),
                            #"offset":offset_per_obj[obj],
                            #"no_verts":object_info[obj],
                            #"batch_index":obj.batch_index,
                            "location":obj.location[:] if self.handedness == 'rh' else invert_y(obj.location)[:],
                            "rotation":obj.rotation_euler[:],
                            "dimensions":obj.dimensions[:],
                            "scale":obj.scale[:],
                            "layers":[lv for lv in obj.layers],
                            "materials":[mat.name for mat in obj.material_slots],
                            "alpha": [ms.material.alpha for ms in obj.material_slots],
                            "diffuse_color": object_get_diffuse_color(obj),
                            "texture":object_get_texture_name(obj),
                            "vertex_groups":[vg.name for vg in obj.vertex_groups],
                            "physics":object_physics_to_json(obj)
                            }
                            for obj in mesh_selection]
        cameras = [{
            "name":obj.name,
            "type":obj.type,
            "location":obj.location[:],
            "rotation":obj.rotation_euler[:],
            "scale":obj.scale[:],
            "angle":obj.data.angle,
            "clip_start":obj.data.clip_start,
            "clip_end":obj.data.clip_end,
            "cam_type":obj.data.type
        }
        for obj in context.selected_objects if obj.type == 'CAMERA']
        lamps = [{
            "name":obj.name,
            "type":obj.type,
            "location":obj.location[:],
            "rotation":obj.rotation_euler[:],
            "scale":obj.scale[:],
            "lamp_type":obj.data.type,
            "use_diffuse":obj.data.use_diffuse,
            "use_specular":obj.data.use_specular,
            "energy":obj.data.energy
        }
        for obj in context.selected_objects if obj.type == 'LAMP']
        speakers = [{
            "name":obj.name,
            "type":obj.type,
            "location":obj.location[:],
            "rotation":obj.rotation_euler[:],
            "scale":obj.scale[:],
            "volume":obj.data.volume,
            "pitch":obj.data.pitch,
            "volume_min":obj.data.volume_min,
            "volume_max":obj.data.volume_max,
            "attenuation":obj.data.attenuation,
            "distance_max":obj.data.distance_max,
            "distance_reference":obj.data.distance_reference
        }
        for obj in context.selected_objects if obj.type == 'SPEAKER']
        emptys = [{
            "name":obj.name,
            "type":obj.type,
            "location":obj.location[:],
            "rotation":obj.rotation_euler[:],
            "scale":obj.scale[:],
            "dupli_type":obj.dupli_type,
            "dupli_group":obj.dupli_group
        }
        for obj in context.selected_objects if obj.type == 'EMPTY']
        armatures = [{
            "name":obj.name,
            "type":obj.type,
            "location":obj.location[:],
            "rotation":obj.rotation_euler[:],
            "scale":obj.scale[:]
        }
        for obj in context.selected_objects if obj.type == 'ARMATURE']
        groups = [{
            "name": grp.name,
            "dupli_offset": grp.dupli_offset[:],
            "objects": [obj.name for obj in grp.objects],
            "layers:": [l for l in grp.layers]
        }
        for grp in bpy.data.groups]
        desc["objects"].extend(cameras)
        desc["objects"].extend(lamps)
        desc["objects"].extend(speakers)
        desc["objects"].extend(emptys)
        desc["objects"].extend(armatures)
        desc["groups"] = groups
        desc["format"]    = [{"type":x.type,"attr":x.attr,"fmt":x.fmt} for x in self.vertex_format]
        desc["no_frames"] = frame_count                             # Number of frames that are exported
        desc["scene"] = {"render":{"layers":[{layer.name:[i for i in layer.layers]} for layer in context.scene.render.layers]}}
        desc["materials"] = [mat.name for mat in bpy.data.materials]
        
        # Save textures
        if self.export_textures:
            for obj in mesh_selection:                              # Only mesh objects have texture slots
                for ms in obj.material_slots:
                    mat = ms.material
                    tex_slot = mat.texture_slots[0]
                if tex_slot != None:
                    image = tex_slot.texture.image
                    image.save_render(base + '/' + image.name,context.scene)
        
        f_desc = open(root + ".json","w")
        
        json.dump(desc,f_desc)
        
        f_desc.close()
        
        # Cleanup: remove dynamic property from class
        del bpy.types.Object.batch_index
        
        return {'FINISHED'}


# Only needed if you want to add into a dynamic menu
def menu_func_export(self, context):
    self.layout.operator(ExportGMSVertexBuffer.bl_idname, text="GM:Studio BLMod (*.json + *.vbx)")


def register():
    bpy.utils.register_class(ExportGMSVertexBuffer)
    bpy.types.INFO_MT_file_export.append(menu_func_export)


def unregister():
    bpy.utils.unregister_class(ExportGMSVertexBuffer)
    bpy.types.INFO_MT_file_export.remove(menu_func_export)


if __name__ == "__main__":
    register()