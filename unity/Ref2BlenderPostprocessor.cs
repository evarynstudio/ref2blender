// Put this file in Assets/Editor/ of your Unity project.
//
// It finishes the import of models exported by Ref2Blender:
//  - a child named "<Model>_Collider" becomes a convex MeshCollider (and stops being drawn);
//  - "<Model>_palette.png" textures are imported unfiltered, so swatch colours do not bleed.
// LODGroups need nothing: Unity builds them itself from the _LOD0, _LOD1... names.

using UnityEditor;
using UnityEngine;

public class Ref2BlenderPostprocessor : AssetPostprocessor
{
    const string ColliderSuffix = "_Collider";
    const string PaletteSuffix = "_palette.png";

    void OnPreprocessTexture()
    {
        if (!assetPath.EndsWith(PaletteSuffix)) return;
        var importer = (TextureImporter)assetImporter;
        importer.filterMode = FilterMode.Point;
        importer.mipmapEnabled = false;
        importer.textureCompression = TextureImporterCompression.Uncompressed;
    }

    void OnPostprocessModel(GameObject root)
    {
        foreach (var child in root.GetComponentsInChildren<Transform>(true))
        {
            if (!child.name.EndsWith(ColliderSuffix)) continue;
            var filter = child.GetComponent<MeshFilter>();
            if (filter == null || filter.sharedMesh == null) continue;

            var collider = child.gameObject.AddComponent<MeshCollider>();
            collider.sharedMesh = filter.sharedMesh;
            collider.convex = true;

            var renderer = child.GetComponent<MeshRenderer>();
            if (renderer != null) Object.DestroyImmediate(renderer);
            Object.DestroyImmediate(filter);
        }
    }
}
