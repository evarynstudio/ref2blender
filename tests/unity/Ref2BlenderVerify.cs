// Test helper, not part of the add-on. Run in batch mode with
//   -executeMethod Ref2BlenderVerify.Run
// It instantiates every model under Assets/Models and writes what Unity
// actually imported to verify_result.txt in the project folder.

using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class Ref2BlenderVerify
{
    static string V(Vector3 v) => $"({v.x:F3}, {v.y:F3}, {v.z:F3})";

    public static void Run()
    {
        var sb = new StringBuilder();
        foreach (var guid in AssetDatabase.FindAssets("t:Model", new[] { "Assets/Models" }))
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            var go = Object.Instantiate(AssetDatabase.LoadAssetAtPath<GameObject>(path));
            sb.AppendLine($"MODEL {path}");
            var lodGroup = go.GetComponentInChildren<LODGroup>();
            sb.AppendLine($"  lodgroup={(lodGroup ? lodGroup.lodCount.ToString() : "none")}");
            foreach (var t in go.GetComponentsInChildren<Transform>(true))
            {
                var line = $"  NODE {t.name} scale={V(t.lossyScale)} rot={V(t.eulerAngles)} pos={V(t.position)}";
                var filter = t.GetComponent<MeshFilter>();
                var collider = t.GetComponent<MeshCollider>();
                var mesh = filter ? filter.sharedMesh : (collider ? collider.sharedMesh : null);
                if (mesh != null)
                {
                    var pts = mesh.vertices.Select(p => t.TransformPoint(p)).ToArray();
                    var min = pts.Aggregate(Vector3.Min);
                    var max = pts.Aggregate(Vector3.Max);
                    // Width (in X) of the geometry at the +Z end and at the -Z end:
                    // the end that narrows to a point is the model's front.
                    var front = pts.Where(p => p.z > max.z - 0.02f).ToArray();
                    var back = pts.Where(p => p.z < min.z + 0.02f).ToArray();
                    line += $" tris={mesh.triangles.Length / 3} min={V(min)} max={V(max)}" +
                            $" widthAtPlusZ={(front.Max(p => p.x) - front.Min(p => p.x)):F3}" +
                            $" widthAtMinusZ={(back.Max(p => p.x) - back.Min(p => p.x)):F3}";
                }
                var renderer = t.GetComponent<MeshRenderer>();
                line += $" renderer={(renderer ? "yes" : "no")}";
                if (collider) line += $" collider=convex:{collider.convex}";
                if (renderer)
                {
                    foreach (var m in renderer.sharedMaterials)
                    {
                        var tex = m != null ? m.mainTexture : null;
                        line += $" material={(m ? m.name : "null")}" +
                                (tex ? $" texture={tex.name}:{tex.filterMode}" : $" color={(m ? m.color.ToString() : "-")}");
                    }
                }
                sb.AppendLine(line);
            }
            Object.DestroyImmediate(go);
        }
        File.WriteAllText("verify_result.txt", sb.ToString());
    }
}
