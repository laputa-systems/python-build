FROM --platform=linux/amd64 alpine@sha256:266f29255458134745f2bf588cb23ed1ed1768b96ff2580a05d70a8aba59e145 AS alpine-amd64
FROM --platform=linux/arm64 alpine@sha256:42d86a3173522de4786cfba0b5d631dbadb3d03a86d218dafb070dafd9809c7e AS alpine-arm64

ARG TARGETARCH
FROM alpine-${TARGETARCH} AS toolchain
ARG TARGETARCH
COPY .cache/bootstrap/apk/${TARGETARCH}/ /opt/bootstrap/apk/
COPY .cache/bootstrap/toolchain/${TARGETARCH}/ /opt/bootstrap/toolchain/
COPY build/patch_llvm_stack.py /opt/bootstrap/patch_llvm_stack.py
COPY bootstrap.lock.json /opt/bootstrap/bootstrap.lock.json

RUN set -eu \
    && mkdir -p /opt/bootstrap \
    && cp /etc/alpine-release /opt/bootstrap/alpine-release \
    && apk info -v | sort > /opt/bootstrap/base-packages.txt \
    && set -- /opt/bootstrap/apk/*.apk /opt/bootstrap/toolchain/* \
    && [ -f "$1" ] \
    && for input in "$@"; do \
        name="${input##*/}"; \
        expected="${name%%.*}"; \
        actual="$(sha256sum "$input")"; \
        actual="${actual%% *}"; \
        [ "$actual" = "$expected" ] || { echo "input digest mismatch: $name" >&2; exit 1; }; \
    done \
    && apk --no-network --repositories-file /dev/null add /opt/bootstrap/apk/*.apk \
    && apk info -v | sort > /opt/bootstrap/packages.txt \
    && python3 -c "\
import json, pathlib, platform; \
root = pathlib.Path('/opt/bootstrap'); lock = json.loads((root / 'bootstrap.lock.json').read_text())['linux_toolchain']; \
arch = platform.machine(); docker_arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}[arch]; \
assert (root / 'alpine-release').read_text().strip() == lock['release']; \
assert lock['images'][docker_arch]['release'] == lock['release']; \
base = dict(value.split('=', 1) for value in lock['base_packages']); \
assert set((root / 'base-packages.txt').read_text().splitlines()) == {name + '-' + version for name, version in base.items()}; \
installed = set((root / 'packages.txt').read_text().splitlines()); \
closure = {item['name']: item['version'] for item in lock['apk_closure'][arch]}; \
expected = {name + '-' + version for name, version in (base | closure).items()}; \
assert installed == expected, {'missing': sorted(expected - installed), 'extra': sorted(installed - expected)}; \
llvm = lock['llvm']['archives'][arch]; \
assert {path.name for path in (root / 'toolchain').iterdir()} == {llvm['sha256'] + '.tar.xz'}; \
(root / 'llvm-archive').write_text(llvm['sha256'] + '.tar.xz\n' + llvm['archive_root'] + '\n')" \
    && for forbidden in gcc g++ cc c++ cpp as ld ld.bfd ld.gold; do \
        ! command -v "$forbidden" >/dev/null || { echo "GCC toolchain command present: $forbidden" >&2; exit 1; }; \
    done \
    && [ ! -e /usr/lib/gcc ] \
    && { read -r archive; read -r archive_root; } < /opt/bootstrap/llvm-archive \
    && mkdir -p /opt/llvm /work/.work/tmp/llvm \
    && tar -xJf "/opt/bootstrap/toolchain/$archive" -C /work/.work/tmp/llvm \
    && [ "$(ls /work/.work/tmp/llvm)" = "$archive_root" ] \
    && cp -a "/work/.work/tmp/llvm/$archive_root/." /opt/llvm/ \
    && rm -rf /work/.work/tmp/llvm \
    && triple="$(uname -m)-unknown-linux-musl" \
    && [ "$(/opt/llvm/bin/clang -print-target-triple)" = "$triple" ] \
    && python3 /opt/bootstrap/patch_llvm_stack.py /opt/llvm/bin \
    && /opt/llvm/bin/llvm-readelf -lW /opt/llvm/bin/lld | grep -q 'GNU_STACK.*0x800000' \
    && /opt/llvm/bin/llvm-readelf -lW /opt/llvm/bin/clang | grep -q 'GNU_STACK.*0x800000' \
    && printf '%s\n' --rtlib=compiler-rt --unwindlib=none -L/opt/llvm/lib '-isystem /usr/include/fortify' \
        > "/opt/llvm/bin/$triple.cfg" \
    && printf '%s\n' -lc++abi -lunwind >> /opt/llvm/bin/clang++.cfg \
    && ln -s llvm-strip /opt/llvm/bin/strip \
    && addgroup -g 1000 builder \
    && adduser -D -u 1000 -G builder builder \
    && mkdir -p /work/.cache \
    && mkdir -p /work/.work/tmp \
    && chown -R builder:builder /work

COPY buildsys/targets.py /opt/bootstrap/targets.py

ENV PATH="/opt/llvm/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    CC=clang CXX=clang++ AR=llvm-ar RANLIB=llvm-ranlib \
    LD=ld.lld NM=llvm-nm STRIP=llvm-strip READELF=llvm-readelf OBJCOPY=llvm-objcopy \
    HOME=/home/builder LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC \
    TMPDIR=/work/.work/tmp \
    PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_CONFIG_FILE=/dev/null SOURCE_DATE_EPOCH=1704067200

USER builder
WORKDIR /work

RUN MARCH="$(PYTHONPATH=/opt/bootstrap python3 -c 'from targets import native_target; print(native_target().cpu_baseline_cflag)')" \
    && printf '%s\n' 'int answer(void) { return 42; }' > "$TMPDIR/lto-library.c" \
    && printf '%s\n' 'extern int answer(void); int main(void) { return answer() != 42; }' > "$TMPDIR/lto-main.c" \
    && clang -O3 $MARCH -fno-omit-frame-pointer -flto=thin -c "$TMPDIR/lto-library.c" -o "$TMPDIR/lto-library.o" \
    && llvm-ar cr "$TMPDIR/liblto.a" "$TMPDIR/lto-library.o" \
    && clang -O3 $MARCH -flto=thin -fuse-ld=lld -Wl,-z,noexecstack "$TMPDIR/lto-main.c" "$TMPDIR/liblto.a" -o "$TMPDIR/lto-smoke" \
    && "$TMPDIR/lto-smoke" \
    && llvm-readelf -l "$TMPDIR/lto-smoke" | grep "/lib/ld-musl-$(uname -m).so.1" \
    && [ "$(llvm-readelf -d "$TMPDIR/lto-smoke" | grep -c NEEDED)" = 1 ] \
    && ! llvm-readelf -p .comment "$TMPDIR/lto-smoke" | grep -F 'GCC:' \
    && clang --version \
    && clang -print-resource-dir \
    && ld.lld --version \
    && python3 --version \
    && rm "$TMPDIR/lto-library.c" "$TMPDIR/lto-main.c" "$TMPDIR/lto-library.o" "$TMPDIR/liblto.a" "$TMPDIR/lto-smoke"

FROM toolchain AS development
COPY --chown=builder:builder build.py /work/build.py
COPY --chown=builder:builder bootstrap.lock.json /work/bootstrap.lock.json
COPY --chown=builder:builder buildsys/ /work/buildsys/
COPY --chown=builder:builder build/ /work/build/
COPY --chown=builder:builder patches/ /work/patches/
COPY --chown=builder:builder tests/ /work/tests/
COPY --chown=builder:builder sources.lock.json /work/sources.lock.json
CMD ["python3", "-m", "unittest", "discover", "-s", "tests"]

FROM development AS sealed
COPY --chown=builder:builder .cache/objects/ /work/.cache/objects/
ARG PYTHON_BUILD_SOURCE_REVISION
ARG PYTHON_BUILD_SOURCE_DIRTY=0
ENV PYTHON_BUILD_BUILD_MODE=sealed \
    PYTHON_BUILD_ISOLATION=buildkit-network-none \
    PYTHON_BUILD_SOURCE_REVISION=${PYTHON_BUILD_SOURCE_REVISION} \
    PYTHON_BUILD_SOURCE_DIRTY=${PYTHON_BUILD_SOURCE_DIRTY}
RUN --network=none python3 build/deps.py \
 && python3 build/cpython.py

FROM sealed AS packaged
RUN --network=none python3 build/package.py \
 && mkdir -p /work/qualification \
 && set -- /work/dist/*/generations/*/*.tar.gz \
 && [ -f "$1" ] \
 && [ "$#" -eq 1 ] \
 && [ -f "$(dirname "$1")/artifact.json" ] \
 && cp "$1" /work/qualification/ \
 && cp "$(dirname "$1")/artifact.json" /work/qualification/artifact.json

FROM alpine-${TARGETARCH} AS runtime
ARG TARGETARCH
COPY --from=packaged /work/qualification/ /qualification/
RUN set -eu \
 && test -z "$(find / -xdev -name 'libgcc_s.so*' -print)" \
 && set -- /qualification/*.tar.gz \
 && [ -f "$1" ] \
 && [ "$#" -eq 1 ] \
 && mkdir -p /opt \
 && tar -xzf "$1" -C /opt \
 && case "$TARGETARCH:$(uname -m)" in amd64:x86_64|arm64:aarch64) ;; *) exit 1 ;; esac \
 && /opt/python/bin/python3 -c "\
import hashlib, json, platform, sys, sysconfig; \
manifest = json.loads(open('/qualification/artifact.json').read()); \
archive = '/qualification/' + sys.argv[1]; \
digest = hashlib.file_digest(open(archive, 'rb'), 'sha256').hexdigest(); \
expected_target = {'amd64': 'x86_64-unknown-linux-musl', 'arm64': 'aarch64-unknown-linux-musl'}['$TARGETARCH']; \
assert manifest['target']['triple'] == expected_target; \
assert digest == manifest['archive']['sha256']; \
expected_version = tuple(int(part) for part in manifest['product']['version'].split('.')); \
assert sys.version_info[:len(expected_version)] == expected_version; \
assert platform.machine() == {'amd64': 'x86_64', 'arm64': 'aarch64'}['$TARGETARCH']; \
import ssl, sqlite3, decimal, uuid, bz2, lzma, zlib, ctypes, readline, dbm, curses; \
import compression.zstd; \
assert 'build/prefix' not in sysconfig.get_config_var('LDFLAGS'); \
print(json.dumps({'python': platform.python_version(), 'archive': sys.argv[1], 'archive_sha256': digest, 'target': manifest['target']['triple'], 'ok': True}))" "${1##*/}" > /qualification/runtime-smoke.json

FROM scratch AS checked
COPY --from=packaged /work/qualification/ /artifacts/
COPY --from=runtime /qualification/runtime-smoke.json /artifacts/runtime-smoke.json
