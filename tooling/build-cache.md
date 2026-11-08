# Verified development bundle cache

The ordinary `libraries.yml` workflow caches completed development-SDK library
bundles between runs. The first cache-enabled run must build missing bundles.
A later WebKit-only patch leaves Mesa and other independent input IDs unchanged.

Cache entries contain only each package's `.tar.gz`, manifest and checksum list,
under its frozen `input_id`. They contain no source trees, object files, SDK,
credentials, failed build outputs or installed writable prefixes. Completed
packages are copied atomically after full bundle verification. The workflow saves
new entries even if a later package fails; canceled jobs do not save a snapshot.
A run that adds no completed packages does not upload another duplicate snapshot.

The existing input ID binds source commit/archive, recipe, probe, patches,
overlays, SDK, builder/tool implementation and dependency input IDs. Snapshot
keys are scoped to the SDK and GitHub branch and include the plan identity plus
run ID/attempt. A broader SDK-matching snapshot is only a transport candidate:
individual bundles are reverified against the current plan and actual pinned
Docker image before extraction and again inventoried before dependency use.
A cache hit never bypasses checksums, archive/path/ELF checks or recipe provenance.
Corrupt or mismatched entries fail verification rather than being treated as a
successful build. Obsolete input IDs are pruned from restored snapshots, so each
saved snapshot contains at most the packages in the current plan. GitHub's cache
retention/eviction policy applies; eviction causes a rebuild, not a false hit.

Only development-SDK Actions enable `MOCHIOS_LIBS_VERIFIED_CACHE=1`. Production
release reuse and the publication approval gate remain unchanged. Development
bundles still cannot be published. This cache does not assert native runtime
compatibility: the existing compile-and-link verification claim is preserved.

On reuse, the log prints `Reusing verified Actions bundle: <library-release>`.
