# Immutable simulation worker image

The Wave 2 worker supports the UR5e translation module. Panda selection fails
closed until the independent Wave 3 adapter is integrated.
Only the image-local allowlist selects its builder; manifests cannot choose
Python imports, executables, devices or network endpoints. Initial capture uses
initialization pins. Exact staged activation and live authority are still
required before an SDK session can execute commands.

The [context preparer](../../../scripts/robotics/prepare_worker_image.py) copies
tracked package sources, the project license, a hash-bearing locked dependency
export, and the selected UR5e/Robotiq model inventory from an existing
checkout. It rejects missing, oversized, symlinked or mismatched model files and
requires a new output directory. It never copies the whole working directory.
The context manifest records actual file hashes and the observed Git commit;
file hashes are authoritative if the checkout has local changes.

The [fixed recipe](../../../scripts/robotics/image/Dockerfile) pins the Python
base by digest and the Debian repository by snapshot date. APT package signature
verification remains enabled. Python packages must match the locked hashes and
have binary distributions. Selected upstream license notices remain beside
their models; Python and Debian package inventories are retained in the image.
The entrypoint always runs the mandatory PID-1 watchdog and then the staged
worker. These package/image inputs require their own qualification; importing a
package or building an image does not establish simulator conformance.

Prepare in an explicit development directory, then build outside the runtime:

```sh
python scripts/robotics/prepare_worker_image.py --models /absolute/menagerie --output /absolute/new-context
docker build --pull=false --iidfile /absolute/image-id.txt /absolute/new-context
```

Retain the context manifest, build log, full immutable image ID, package
inventories and actual host inspection with the candidate. Failed builds keep
their logs and contexts. The runtime's trusted inventory admits an exact local
image ID; it cannot invoke the builder, pull an image, or accept a moving tag.
The supervisor runs a non-root worker with a read-only filesystem, software EGL,
bounded cgroups, no network or devices, and only the private episode bootstrap
and scoped IPC mount. No database, Docker socket, evaluator signing key, global
artifact directory or human approval is included in the worker image.

Real isolated startup, current trusted conformance installation, actual
observations, owned cleanup and all required safety/replay evidence remain
separate integration gates. A construction image is not an admission grant.
