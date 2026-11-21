// What Sonarr's remote path mapping really does at three edges, run against
// Sonarr's OWN OsPath.cs at a pinned release tag. Receipts for
// docs/measurements/2026-11-21-arr-path-mapping-edges.md (lintarr#68).
//
// Run it with docs/68-ospath-probe.sh, which fetches OsPath.cs at the tag,
// compiles the two together and runs the result. Nothing in src/ or tests/
// uses this file; it is a receipt, not a dependency.
//
// Main is the body of RemotePathMappingService.RemapRemoteToLocal for one
// mapping -- the only three lines of Sonarr quoted here rather than fetched:
//
//     if (host.Equals(mapping.Host, StringComparison.InvariantCultureIgnoreCase)
//         && new OsPath(mapping.RemotePath).Contains(remotePath))
//     {
//         var localPath = new OsPath(mapping.LocalPath) + (remotePath - new OsPath(mapping.RemotePath));
//     }

using System;
using NzbDrone.Common.Disk;

// OsPath.cs calls two extension methods from NzbDrone.Common.Extensions.
// Neither decides anything measured below: IsNullOrWhiteSpace is the framework
// call of the same name, and IsPathValid is only read by OsPath.IsValid, which
// nothing here touches.
namespace NzbDrone.Common.Extensions
{
    public enum PathValidationType { CurrentOs, AnyOs }

    public static class StringExtensions
    {
        public static bool IsNullOrWhiteSpace(this string text) => string.IsNullOrWhiteSpace(text);

        public static bool IsPathValid(this string text, PathValidationType type) =>
            !string.IsNullOrWhiteSpace(text);
    }
}

public static class Probe
{
    static string Remap(string mappingHost, string remotePath, string localPath, string raw)
    {
        var osRaw = new OsPath(raw);
        if (osRaw.IsEmpty)
        {
            return "(empty input, returned as-is)";
        }

        if ("qbit".Equals(mappingHost, StringComparison.InvariantCultureIgnoreCase)
            && new OsPath(remotePath).Contains(osRaw))
        {
            return "[" + (new OsPath(localPath) + (osRaw - new OsPath(remotePath))).FullPath + "]";
        }

        return "no match, path unchanged: [" + osRaw.FullPath + "]";
    }

    static void Case(string remote, string local, string raw)
    {
        string got;
        try
        {
            got = Remap("QBIT", remote, local, raw);
        }
        catch (Exception e)
        {
            got = "THROWS " + e.GetType().Name + ": " + e.Message;
        }

        Console.WriteLine("  remote {0,-18} local {1,-16} raw {2,-26} -> {3}",
                          "'" + remote + "'", "'" + local + "'", "'" + raw + "'", got);
    }

    public static void Main()
    {
        Console.WriteLine("A Windows remote path folds case AND separators:");
        Case(@"C:\Downloads\", "/data/torrents", @"c:\downloads\tv");
        Case(@"c:/downloads", "/data/torrents", @"C:\DOWNLOADS\tv\s1");
        Case(@"C:\Downloads", "/data/torrents", @"c:\downloads");
        Console.WriteLine("A POSIX remote path folds neither:");
        Case("/DL", "/a", "/dl/x");
        Case("/dl", "/a", "/DL/x");
        Case("/dl", "/a", "/dl/x");
        Case("/DL", "/a", @"/dl\x");
        Console.WriteLine("A root remote path swallows the whole path:");
        Case("/", "/mnt", "/data");
        Case("/", "/mnt", "/data/tv/x");
        Case("//", "/mnt", "/data");
        Case(@"\\", "/mnt", "/data");
        Console.WriteLine("A blank local path (which the arr's own API refuses):");
        Case("/dl", "", "/dl/x");
        Case("/dl", "   ", "/dl/x");
        Case("/dl", "", "/dl");
        Console.WriteLine("An unrooted path on either side never matches:");
        Case("", "/a", "/dl/x");
        Case("dl", "/a", "/dl/x");
        Case("/dl", "/a", "dl/x");
    }
}
