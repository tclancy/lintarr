#!/usr/bin/env bash
# Re-derive docs/measurements/2026-11-21-arr-path-mapping-edges.md: fetch
# Sonarr's OsPath.cs at a pinned release tag, compile it with docs/68-ospath-probe.cs
# and print what the mapping arithmetic does at each edge.
#
#   docs/68-ospath-probe.sh [tag] [repo]      # default v4.0.20.3014, Sonarr/Sonarr
#
# Needs a C# compiler and runtime; `brew install mono` provides both. Sonarr is
# GPL-3.0, so its source is fetched at run time and never vendored here.
set -euo pipefail

TAG=${1:-v4.0.20.3014}
REPO=${2:-Sonarr/Sonarr}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "Sonarr OsPath + RemapRemoteToLocal, $REPO $TAG"
curl -fsS "https://raw.githubusercontent.com/$REPO/$TAG/src/NzbDrone.Common/Disk/OsPath.cs" \
  -o "$WORK/OsPath.cs"
echo "OsPath.cs: $(wc -c <"$WORK/OsPath.cs" | tr -d ' ') bytes, sha256 $(shasum -a 256 "$WORK/OsPath.cs" | cut -c1-16)"
csc -nologo -langversion:latest -out:"$WORK/probe.exe" "$WORK/OsPath.cs" "$HERE/68-ospath-probe.cs"
mono "$WORK/probe.exe"
