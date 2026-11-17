#!/usr/bin/env bash
# The upstream Pi recipe extracts the same prebuilt Electron resources. No extra
# Satellite service or updater is installed: OMI owns the child process.
set -Eeuo pipefail
prefix=${1:-/opt/omi/satellite}
prepare_only=${2:-}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
version=3.8.0
build=804-8831637
node_version=24.22.2
arch=$(dpkg --print-architecture)
case "$arch" in
    arm64) target=arm64; node_sha=eb187837a2230d4047161ac0be008584e16b46f726e9c9490ee35dc6b17d9ba8 ;;
    amd64) target=x64; node_sha=48cc0e21e048790e54c0cf5749c2725005313d652651c40b7a21feb9697aaf75 ;;
    *) echo "Unsupported Satellite architecture: $arch" >&2; exit 1;;
esac
destination="$prefix/$version-$build-$arch"
if [[ ! -f "$destination/.complete" ]]; then
    [[ ! -e "$destination" ]] || { echo "Incomplete Satellite build: $destination; move aside and retry" >&2; exit 1; }
    mkdir -p "$destination"
    temp=$(mktemp -d /tmp/omi-satellite.XXXXXXXX)
    trap 'rm -rf -- "$temp"' EXIT
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
        "https://cf-pub.bitfocus.io/companion/companion-satellite/companion-satellite-$target-$build.tar.gz" -o "$temp/satellite.tar.gz"
    expected=$(python3 - "$script_dir/../deploy/satellite-lock.json" "$arch" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['archives'][sys.argv[2]]['sha256'])
PY
)
    printf '%s  %s\n' "$expected" "$temp/satellite.tar.gz" | sha256sum -c -
    mkdir "$temp/archive"
    tar -xzf "$temp/satellite.tar.gz" --strip-components=1 -C "$temp/archive"
    python3 "$script_dir/extract-asar.py" "$temp/archive/resources/app.asar" "$destination/satellite"
    mkdir "$destination/webui"
    cp -a "$temp/archive/resources/webui" "$destination/webui/dist"
    for resource in node-runtimes assets modules; do
        cp -a "$temp/archive/resources/$resource" "$destination/$resource"
    done
    if [[ -f "$temp/archive/resources/surface-entrypoint.mjs" ]]; then
        cp "$temp/archive/resources/surface-entrypoint.mjs" "$destination/satellite/dist/surface-entrypoint.mjs"
    fi
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
        "https://nodejs.org/dist/v$node_version/node-v$node_version-linux-$target.tar.xz" -o "$temp/node.tar.xz"
    printf '%s  %s\n' "$node_sha" "$temp/node.tar.xz" | sha256sum -c -
    mkdir "$destination/node"
    tar -xJf "$temp/node.tar.xz" --strip-components=1 -C "$destination/node"
    cat > "$destination/companion-satellite" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export NODE_ENV=production
exec "$directory/node/bin/node" "$directory/satellite/dist/main.js" "$@"
SH
    chmod 755 "$destination/companion-satellite"
    python3 - "$destination" "$version" "$build" <<'PY'
import json,pathlib,sys
root = pathlib.Path(sys.argv[1])
package = json.loads((root/'satellite/package.json').read_text())
if package['version'].lstrip('v') != sys.argv[2]: raise SystemExit('Wrong Satellite package version')
required = ['satellite/dist/main.js','satellite/dist/surface-entrypoint.mjs','webui/dist/index.html',
            'modules/elgato-stream-deck/companion/manifest.json','node/bin/node']
for entry in required:
    if not (root/entry).is_file(): raise SystemExit('Missing prepared Satellite file: '+entry)
manifest = json.loads((root/'modules/elgato-stream-deck/companion/manifest.json').read_text())
runtimes = json.loads((root/'assets/nodejs-versions.json').read_text())
for runtime in runtimes:
    if not (root/'node-runtimes'/runtime/'bin/node').is_file(): raise SystemExit('Missing surface runtime '+runtime)
(root/'omi-satellite.json').write_text(json.dumps(dict(version=sys.argv[2],build=sys.argv[3],executable='companion-satellite')))
PY
    "$destination/node/bin/node" --check "$destination/satellite/dist/main.js"
    # Import production native modules on this actual target before activation.
    (cd "$destination/satellite" && "$destination/node/bin/node" --input-type=module -e \
        "await import('usb'); await import('node-hid'); await import('@julusian/image-rs'); await import('@napi-rs/canvas')")
    touch "$destination/.complete"
fi
if [[ "$prepare_only" != --prepare-only ]]; then
    ln -sfn "$destination" "$prefix/current.new"
    mv -Tf "$prefix/current.new" "$prefix/current"
fi
