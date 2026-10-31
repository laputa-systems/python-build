"""Per-module kernels for the Rust-for-CPython performance goals.

Each kernel exercises public behavior routed to Rust, with deterministic
inputs, so the pristine control and the Rust candidate do identical work.
The module runner runs
this file under a stage interpreter, one fresh process per sample:

    python3.16 -s -P perf_modules.py measure <route> --iterations N

The process reports, as one JSON line:

- `cpu_seconds_per_iteration`: `time.process_time()` over the measured loop;
- `load_footprint_bytes`: the route's fixed physical footprint: imports,
  lazily loaded extensions, and first-call caches, estimated as the growth
  of the first setup-and-call minus the growth of a second one (which
  repeats only per-instance inputs and state);
- `load_resident_bytes`: the same estimate in resident size;
- `working_peak_bytes`: the kernel loop's footprint peak above the
  footprint at loop start (`ri_interval_max_phys_footprint`, reset first);
- `digest`: a hash of the warmup output; both interpreters must agree.

Inputs are built in `setup` outside every measured interval. The file uses
only the standard library and is not imported by the controller.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from types import SimpleNamespace

_FIELDS = ["ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins", "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime", "ri_proc_exit_abstime", "ri_child_user_time", "ri_child_system_time", "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups", "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread", "ri_diskio_byteswritten", "ri_cpu_time_qos_default", "ri_cpu_time_qos_maintenance", "ri_cpu_time_qos_background", "ri_cpu_time_qos_utility", "ri_cpu_time_qos_legacy", "ri_cpu_time_qos_user_initiated", "ri_cpu_time_qos_user_interactive", "ri_billed_system_time", "ri_serviced_system_time", "ri_logical_writes", "ri_lifetime_max_phys_footprint", "ri_instructions", "ri_cycles", "ri_billed_energy", "ri_serviced_energy", "ri_interval_max_phys_footprint", "ri_runnable_time"]


class _RUsageInfoV4(ctypes.Structure):
    # Exact <sys/resource.h> rusage_info_v4 layout: a short buffer would let
    # libproc write past Python-owned memory.
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in _FIELDS]


if sys.platform == "darwin":
    _LIBSYSTEM = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    _LIBSYSTEM.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    _LIBSYSTEM.proc_rlimit_control.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    _RUSAGE_INFO_V4 = 4
    _RLIMIT_FOOTPRINT_INTERVAL = 4
    _FOOTPRINT_INTERVAL_RESET = 1


def _linux_usage():
    values = {}
    with open("/proc/self/status") as status:
        for line in status:
            if line.startswith(("VmRSS:", "VmHWM:")):
                name, value, *_ = line.split()
                values[name.rstrip(":")] = int(value) * 1024
    resident = values.get("VmRSS", 0)
    return SimpleNamespace(ri_user_time=0, ri_system_time=0,
                           ri_phys_footprint=resident, ri_resident_size=resident,
                           ri_interval_max_phys_footprint=values.get("VmHWM", resident))


def _usage():
    if sys.platform == "darwin":
        info = _RUsageInfoV4()
        if _LIBSYSTEM.proc_pid_rusage(os.getpid(), _RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
            raise OSError(ctypes.get_errno(), "proc_pid_rusage failed")
        return info
    return _linux_usage()


def _reset_interval_peak() -> None:
    if sys.platform == "darwin" and _LIBSYSTEM.proc_rlimit_control(
            os.getpid(), _RLIMIT_FOOTPRINT_INTERVAL,
            ctypes.c_void_p(_FOOTPRINT_INTERVAL_RESET)) != 0:
        raise OSError(ctypes.get_errno(), "footprint interval reset failed")


def _text(count: int, seed: int = 7) -> list[str]:
    words = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
             "iota", "kappa", "lambda", "mu", "catalog", "request", "path", "value")
    return [" ".join(words[(i * 7 + j * seed) % len(words)] for j in range(6 + i % 9))
            for i in range(count)]


def _blob(size: int) -> bytes:
    # Compressible but not trivial: repeated records with a varying counter.
    parts, total, index = [], 0, 0
    while total < size:
        chunk = f"record={index:08d};name=item-{index % 97};value={index * 31 % 1009}\n".encode()
        parts.append(chunk)
        total += len(chunk)
        index += 1
    return b"".join(parts)[:size]


# ---------------------------------------------------------------- kernels
# Each kernel returns (imports, setup) where setup() -> run and run() returns
# a deterministic value for the digest. Imports are the public modules whose
# load cost counts toward the route.

def k_urllib_parse():
    def setup():
        from urllib.parse import parse_qsl, quote, unquote, urlencode
        values = [f"/catalog/{t}/ü?x=1&y={i}" for i, t in enumerate(_text(200))]
        queries = [urlencode({"q": t, "page": i, "tag": "a b&c"}) for i, t in enumerate(_text(200))]

        def run():
            quoted = [quote(value) for value in values]
            return ([unquote(value) for value in quoted][-1],
                    [parse_qsl(query) for query in queries][-1])
        return run
    return ("urllib.parse",), setup


def k_json():
    def setup():
        import json
        document = {"items": [{"id": i, "name": t, "price": i * 1.25, "tags": t.split()[:3],
                               "active": i % 3 == 0, "meta": None} for i, t in enumerate(_text(400))]}
        text = json.dumps(document)

        def run():
            return len(json.dumps(document)), json.loads(text)["items"][-1]
        return run
    return ("json",), setup


def k_pickle():
    def setup():
        import io
        import pickle
        graph = {"rows": [(i, str(i), float(i), [i, i + 1], {"k": i}) for i in range(2000)],
                 "text": _text(200)}
        data = pickle.dumps(graph)
        compact = {"items": [None, True, False, -7, 42, 1.25, b"native", "rust ü"]}

        def run():
            stream = io.BytesIO()
            pickle.dump(graph, stream)
            stream.seek(0)
            compact_stream = io.BytesIO()
            pickle.dump(compact, compact_stream)
            compact_stream.seek(0)
            return (len(pickle.dumps(graph)), pickle.loads(data)["rows"][-1],
                    pickle.load(stream)["text"][-1],
                    pickle.loads(pickle.dumps(compact))["items"],
                    pickle.load(compact_stream)["items"])
        return run
    return ("pickle",), setup


def k_csv():
    def setup():
        import csv
        import io
        rows = [[str(i), t, f"{i * 0.5:.2f}", "a,b" if i % 5 == 0 else "plain"]
                for i, t in enumerate(_text(1500))]

        def run():
            buffer = io.StringIO()
            csv.writer(buffer).writerows(rows)
            return list(csv.reader(io.StringIO(buffer.getvalue())))[-1]
        return run
    return ("csv",), setup


def k_tomllib():
    def setup():
        import tomllib
        lines = ['title = "catalog"', "[owner]", 'name = "example"', "dob = 1979-05-27T07:32:00Z"]
        for i in range(200):
            lines += ["[[items]]", f"id = {i}", f'name = "item-{i}"', f"price = {i}.5",
                      f"tags = [\"a\", \"b\", \"{i}\"]"]
        text = "\n".join(lines) + "\n"

        def run():
            return tomllib.loads(text)["items"][-1]
        return run
    return ("tomllib",), setup


def k_email():
    def setup():
        import email
        from email import policy
        headers = "".join(f"X-Header-{i}: value {t}\n" for i, t in enumerate(_text(20)))
        message = ("From: Alice <alice@example.com>\nTo: Bob <bob@example.com>\n"
                   "Subject: catalog update\nMIME-Version: 1.0\n"
                   "Content-Type: text/plain; charset=utf-8\n" + headers + "\n"
                   + "\n".join(_text(40)) + "\n")

        def run():
            parsed = [email.message_from_string(message, policy=policy.default) for _ in range(20)]
            return parsed[-1]["Subject"], str(parsed[-1]["From"]), len(parsed[-1].get_content())
        return run
    return ("email", "email.policy"), setup


def k_xml_etree():
    def setup():
        import xml.etree.ElementTree as ET
        body = "".join(f'<item id="{i}" kind="k{i % 5}"><name>{t}</name><price>{i}.5</price></item>'
                       for i, t in enumerate(_text(500)))
        text = f"<catalog>{body}</catalog>"

        def run():
            root = ET.fromstring(text)
            return len(root), len(ET.tostring(root))
        return run
    return ("xml.etree.ElementTree",), setup


def k_re():
    def setup():
        import re
        patterns = [r"\b(\w+)@(\w+)\.com\b", r"^record=(\d+);name=([a-z-]+\d*)", r"[A-Z][a-z]+ \d{4}",
                    r"(?:GET|POST) (/\S*) HTTP/1\.[01]", r"value=(\d+)$", r"\d+\.\d+\.\d+\.\d+"]
        lines = _blob(40_000).decode().splitlines()

        def run():
            re.purge()
            compiled = [re.compile(pattern) for pattern in patterns]
            pattern_counts = [sum(1 for line in lines if regex.search(line)) for regex in compiled]
            public_counts = [sum(1 for line in lines if re.search(pattern, line)) for pattern in patterns]
            return pattern_counts, public_counts
        return run
    return ("re",), setup


def k_base64():
    def setup():
        import base64
        data = _blob(256 * 1024)
        encoded = base64.b64encode(data)

        def run():
            return (len(base64.b64encode(data)), len(base64.b64decode(encoded)),
                    len(base64.urlsafe_b64encode(data[:65536])), len(base64.b32encode(data[:16384])))
        return run
    return ("base64",), setup


def k_binascii():
    def setup():
        import binascii
        data = _blob(256 * 1024)
        hexed = binascii.hexlify(data)

        def run():
            return (len(binascii.hexlify(data)), len(binascii.unhexlify(hexed)),
                    binascii.crc32(data), binascii.crc_hqx(data[:65536], 0))
        return run
    return ("binascii",), setup


def k_zlib():
    def setup():
        import zlib
        data = _blob(1 << 20)
        packed = zlib.compress(data)

        def run():
            stream = zlib.decompressobj()
            parts = [stream.decompress(packed[i:i + 4096]) for i in range(0, len(packed), 4096)]
            compressor = zlib.compressobj()
            streamed = compressor.compress(data[:262144]) + compressor.flush()
            return (len(zlib.decompress(zlib.compress(data))), len(zlib.decompress(packed)),
                    sum(map(len, parts)) + len(stream.flush()), len(zlib.decompress(streamed)))
        return run
    return ("zlib",), setup


def k_gzip():
    def setup():
        import gzip
        import io
        data = _blob(512 * 1024)
        packed = gzip.compress(data, mtime=0)

        def run():
            buffer = io.BytesIO()
            with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as handle:
                handle.write(data)
            with gzip.GzipFile(fileobj=io.BytesIO(packed)) as handle:
                read = handle.read()
            return (len(gzip.decompress(gzip.compress(data, mtime=0))), len(gzip.decompress(packed)),
                    len(gzip.decompress(buffer.getvalue())), len(read))
        return run
    return ("gzip",), setup


def k_zipfile():
    def setup():
        import io
        import zipfile
        files = [(f"pkg/module_{i}.py", _blob(8192 + i * 64)) for i in range(40)]

        def run():
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in files:
                    archive.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), data,
                                     compress_type=zipfile.ZIP_DEFLATED)
            with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
                return len(archive.infolist()), sum(len(archive.read(info)) for info in archive.infolist())
        return run
    return ("zipfile",), setup


def k_tarfile():
    def setup():
        import io
        import tarfile
        files = [(f"pkg/file_{i}.txt", _blob(1024 + i * 16)) for i in range(200)]

        def run():
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name, data in files:
                    info = tarfile.TarInfo(name)
                    info.size, info.mtime = len(data), 1_700_000_000
                    archive.addfile(info, io.BytesIO(data))
            buffer.seek(0)
            with tarfile.open(fileobj=buffer, mode="r") as archive:
                members = archive.getmembers()
            return len(buffer.getvalue()), len(members), members[-1].name
        return run
    return ("tarfile",), setup


def k_pathlib():
    def setup():
        from pathlib import Path, PurePosixPath
        root = Path(sys.prefix) / "lib"
        probes = [root / name for name in sorted(os.listdir(root))[:40]] + [root / "missing-1", root / "x/y"]
        texts = [f"/srv/{t.replace(' ', '/')}/file.{i % 7}.txt" for i, t in enumerate(_text(300))]

        def run():
            parsed = [PurePosixPath(text) for text in texts]
            flags = [(path.exists(), path.is_dir(), path.is_file()) for path in probes]
            return str(parsed[-1].parent), parsed[-1].suffix, flags[-3:]
        return run
    return ("pathlib",), setup


def k_os_path():
    def setup():
        import posixpath
        texts = [f"/srv//{t.replace(' ', '/./')}/../file.{i % 7}.txt" for i, t in enumerate(_text(500))]

        def run():
            normalized = [posixpath.normpath(text) for text in texts]
            joined = [posixpath.join("/base", "a", text[1:]) for text in texts]
            return (normalized[-1], posixpath.split(joined[-1]), posixpath.splitroot(texts[-1]))
        return run
    return ("os.path", "posixpath"), setup


def k_shutil():
    def setup():
        import io
        import shutil
        import tempfile
        data = _blob(1 << 20)
        base = tempfile.mkdtemp(prefix="perf-shutil-")
        source = os.path.join(base, "source")
        for i in range(30):
            os.makedirs(os.path.join(source, f"d{i % 5}"), exist_ok=True)
            with open(os.path.join(source, f"d{i % 5}", f"f{i}.txt"), "wb") as handle:
                handle.write(data[: 2048 + i])
        counter = iter(range(1 << 30))

        def run():
            target = io.BytesIO()
            shutil.copyfileobj(io.BytesIO(data), target)
            destination = os.path.join(base, f"copy-{next(counter)}")
            shutil.copytree(source, destination)
            copied = sum(len(files) for _, _, files in os.walk(destination))
            shutil.rmtree(destination)
            return len(target.getvalue()), copied
        return run
    return ("shutil",), setup


def k_importlib_metadata():
    def setup():
        import importlib.metadata
        import tempfile
        base = tempfile.mkdtemp(prefix="perf-metadata-")
        for i in range(30):
            info = os.path.join(base, f"pkg{i}-1.{i}.0.dist-info")
            os.makedirs(info)
            with open(os.path.join(info, "METADATA"), "w") as handle:
                handle.write(f"Metadata-Version: 2.1\nName: pkg{i}\nVersion: 1.{i}.0\n"
                             f"Summary: package {i}\nRequires-Dist: pkg{(i + 1) % 30}\n\n{_text(5)[0]}\n")

        def run():
            dists = list(importlib.metadata.distributions(path=[base]))
            names = sorted((d.metadata["Name"], d.version, tuple(d.requires or ())) for d in dists)
            return names[-1], len(names)
        return run
    return ("importlib.metadata",), setup


def k_hashlib():
    def setup():
        import hashlib
        data = _blob(1 << 20)

        def run():
            out = []
            for name in ("sha256", "sha1", "md5", "sha512", "blake2b", "sha3_256"):
                digest = hashlib.new(name)
                for i in range(0, len(data), 65536):
                    digest.update(data[i:i + 65536])
                out.append(digest.hexdigest())
            return out
        return run
    return ("hashlib",), setup


def k_hmac():
    def setup():
        import hmac
        messages = [text.encode() for text in _text(2000)]

        def run():
            return (hmac.new(b"secret-key", b"".join(messages), "sha256").hexdigest(),
                    [hmac.digest(b"k", message, "sha256") for message in messages][-1].hex())
        return run
    return ("hmac",), setup


def k_uuid():
    def setup():
        import uuid
        texts = [str(uuid.UUID(int=(i * 0x9E3779B97F4A7C15) % (1 << 128))) for i in range(2000)]

        def run():
            parsed = [uuid.UUID(text) for text in texts]
            generated = [uuid.uuid4() for _ in range(500)]
            return str(parsed[-1]), parsed[-1].hex, len({g.version for g in generated})
        return run
    return ("uuid",), setup


def k_datetime():
    def setup():
        from datetime import date, datetime, timedelta
        stamps = [f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}T{i % 24:02d}:{i % 60:02d}:{(i * 7) % 60:02d}"
                  for i in range(2000)]
        step = timedelta(days=1, seconds=37)

        def run():
            parsed = [datetime.fromisoformat(text) for text in stamps]
            moved = [value + step for value in parsed]
            days = [date(2024, 1, 1) + timedelta(days=i) for i in range(500)]
            return moved[-1].isoformat(), days[-1].isoformat(), [value.isoformat() for value in parsed][-1]
        return run
    return ("datetime",), setup


def k_decimal():
    def setup():
        from decimal import Decimal
        values = [Decimal(i * 7919) for i in range(3000)]

        def run():
            total = Decimal(0)
            product = Decimal(1)
            for value in values:
                total = total + value
                product = (product * value) if value else product
            return str(total), len(str(product))
        return run
    return ("decimal",), setup


def k_sqlite3():
    def setup():
        import sqlite3
        connection = sqlite3.connect(":memory:")
        connection.execute("create table items (id integer, name text, price real, blob blob)")
        connection.executemany("insert into items values (?, ?, ?, ?)",
                               [(i, t, i * 0.5, t.encode()) for i, t in enumerate(_text(3000))])

        def run():
            rows = connection.execute("select id, name, price, blob from items").fetchall()
            return len(rows), rows[-1]
        return run
    return ("sqlite3",), setup


def k_io():
    def setup():
        import io
        data = _blob(1 << 20)
        crlf = data.replace(b"\n", b"\r\n")
        text = data.decode()

        def run():
            reader = io.BufferedReader(io.BytesIO(data), buffer_size=8192)
            lines = sum(1 for _ in reader)
            copied = io.BufferedReader(io.BytesIO(data)).read()
            decoded = io.TextIOWrapper(io.BufferedReader(io.BytesIO(crlf)), encoding="utf-8", newline=None).read()
            sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\r\n", write_through=True)
            sink.write(text)
            return lines, len(copied), len(decoded), sink.buffer.tell()
        return run
    return ("io",), setup


def k_logging():
    def setup():
        import io
        import logging
        stream = io.StringIO()
        logger = logging.getLogger(f"perf.kernel.{id(stream)}")
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s %(value)d"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        def run():
            stream.seek(0)
            stream.truncate()
            for i in range(2000):
                logger.info("item %s processed", i, extra={"value": i})
            return len(stream.getvalue())
        return run
    return ("logging",), setup


def k_asyncio():
    def setup():
        import asyncio

        def run():
            loop = asyncio.new_event_loop()
            counter = [0]

            def tick():
                counter[0] += 1
            for _ in range(5000):
                loop.call_soon(tick)
            for i in range(2000):
                loop.call_later(0, tick)
            loop.call_later(0.001, loop.stop)
            loop.run_forever()
            loop.close()
            return counter[0]
        return run
    return ("asyncio",), setup


class _FakeSocket:
    def __init__(self, data: bytes = b""):
        import io
        self._data, self.sent = data, bytearray()
        self._io = io

    def makefile(self, mode, *args, **kwargs):
        return self._io.BytesIO(self._data)

    def sendall(self, data):
        self.sent += data

    def close(self):
        pass


def k_http_client():
    def setup():
        import http.client
        body = b"x" * 512
        response = (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 512\r\n"
                    b"X-Trace: abc\r\n\r\n" + body)

        def run():
            statuses = []
            for _ in range(300):
                reply = http.client.HTTPResponse(_FakeSocket(response))
                reply.begin()
                statuses.append((reply.status, len(reply.read())))
            connection = http.client.HTTPConnection("example.invalid")
            connection.sock = _FakeSocket()
            for i in range(300):
                connection.putrequest("GET", f"/catalog/{i}?q=x")
                connection.putheader("Accept", "text/plain")
                connection.endheaders()
                connection._HTTPConnection__state = "Idle"
            return statuses[-1], len(connection.sock.sent)
        return run
    return ("http.client",), setup


def k_ipaddress():
    def setup():
        import ipaddress
        v4 = [f"10.{i % 256}.{(i * 7) % 256}.{(i * 13) % 256}" for i in range(2000)]
        v6 = [f"2001:db8:{i:x}::{(i * 7) % 65536:x}" for i in range(1000)]
        nets = [f"192.168.{i % 256}.0/{16 + i % 16}" for i in range(500)]

        def run():
            addresses = [ipaddress.ip_address(text) for text in v4 + v6]
            networks = [ipaddress.ip_network(text, strict=False) for text in nets]
            return (str(addresses[-1]), int(addresses[0]),
                    str(networks[-1].network_address), str(networks[-1].broadcast_address))
        return run
    return ("ipaddress",), setup


def k_socket():
    def setup():
        import socket
        v4 = [f"10.{i % 256}.{(i * 7) % 256}.1" for i in range(2000)]
        v6 = [f"2001:db8::{i:x}" for i in range(1000)]
        left, right = socket.socketpair()
        payload = _blob(4096)

        def run():
            packed4 = [socket.inet_pton(socket.AF_INET, text) for text in v4]
            packed6 = [socket.inet_pton(socket.AF_INET6, text) for text in v6]
            back = [socket.inet_ntop(socket.AF_INET6, value) for value in packed6]
            received = 0
            for _ in range(200):
                left.send(payload)
                received += len(right.recv(8192))
            return len(packed4), back[-1], received
        return run
    return ("socket",), setup


def k_ssl():
    def setup():
        import ssl
        certs = os.path.join(sys.prefix, "lib", f"python{sys.version_info[0]}.{sys.version_info[1]}",
                             "test", "certdata", "keycert.pem")
        data = _blob(16384)

        def run():
            server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server.load_cert_chain(certs)
            client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client.check_hostname = False
            client.verify_mode = ssl.CERT_NONE
            c_in, c_out, s_in, s_out = ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO()
            c = client.wrap_bio(c_in, c_out)
            s = server.wrap_bio(s_in, s_out, server_side=True)
            for _ in range(20):
                for side in (c, s):
                    try:
                        side.do_handshake()
                    except ssl.SSLWantReadError:
                        pass
                s_in.write(c_out.read())
                c_in.write(s_out.read())
            c.write(data)
            s_in.write(c_out.read())
            received = s.read(len(data))
            return len(received), c.version()
        return run
    return ("ssl",), setup


def k_subprocess():
    def setup():
        import subprocess
        data = _blob(65536)

        def run():
            launched = [subprocess.run(["/usr/bin/true"], check=False).returncode for _ in range(10)]
            launched.append(subprocess.run(["/usr/bin/true"], close_fds=False, check=False).returncode)
            echoed = subprocess.run(["/bin/cat"], input=data, capture_output=True, check=False).stdout
            return sum(launched), len(echoed)
        return run
    return ("subprocess",), setup


def k_multiprocessing():
    def setup():
        import multiprocessing
        left, right = multiprocessing.Pipe()
        records = [(i, str(i), [i] * 5) for i in range(1000)]
        queue = multiprocessing.Queue()

        def run():
            received = []
            for record in records:
                left.send(record)
                received.append(right.recv())
            queued = []
            for record in records[:200]:
                queue.put(record)
                queued.append(queue.get())
            pool = multiprocessing.Pool(processes=1)
            try:
                mapped = pool.map(abs, (-1,), chunksize=1)
            finally:
                pool.close()
                pool.join()
            return received[-1], queued[-1], tuple(mapped)
        return run
    return ("multiprocessing",), setup


def k_concurrent_futures():
    def setup():
        from concurrent.futures import Future

        def run():
            seen = [0]

            def callback(future):
                seen[0] += future.result()
            futures = []
            for i in range(3000):
                future = Future()
                future.add_done_callback(callback)
                future.set_running_or_notify_cancel()
                future.set_result(i)
                futures.append(future)
            return seen[0], futures[-1].done()
        return run
    return ("concurrent.futures",), setup


def k_configparser():
    def setup():
        import configparser
        import io
        text = "\n".join(f"[section{i}]\nname = item {i}\nvalue = {i * 3}\npath = /srv/{i}\n" for i in range(200))

        def run():
            parser = configparser.ConfigParser()
            parser.read_string(text)
            out = io.StringIO()
            parser.write(out)
            return parser["section199"]["value"], len(out.getvalue())
        return run
    return ("configparser",), setup


def k_plistlib():
    def setup():
        import plistlib
        document = {"items": [{"id": i, "name": t, "flag": i % 2 == 0, "data": t.encode()[:8]}
                              for i, t in enumerate(_text(400))]}
        xml, binary = plistlib.dumps(document), plistlib.dumps(document, fmt=plistlib.FMT_BINARY)

        def run():
            return (len(plistlib.dumps(document)), len(plistlib.dumps(document, fmt=plistlib.FMT_BINARY)),
                    plistlib.loads(xml)["items"][-1]["id"], plistlib.loads(binary)["items"][-1]["name"])
        return run
    return ("plistlib",), setup


def k_struct():
    def setup():
        import struct
        record = struct.Struct("<IhqdI8s")
        values = [(i, i % 30000, i * 7, i * 0.5, i ^ 0xFFFF, b"abcdefgh") for i in range(5000)]
        packed = b"".join(record.pack(*value) for value in values)

        def run():
            blob = b"".join(struct.pack("<IhqdI8s", *value) for value in values)
            unpacked = [struct.unpack_from("<IhqdI8s", packed, i * record.size) for i in range(len(values))]
            return len(blob), unpacked[-1], sum(1 for _ in record.iter_unpack(packed))
        return run
    return ("struct",), setup


def k_marshal():
    def setup():
        import marshal
        source = "\n".join(f"def f{i}(x, y={i}):\n    return [x + y, {i!r}, 'text{i}', {i}.5]" for i in range(200))
        code = compile(source, "kernel.py", "exec")
        data_record = {"rows": [(i, str(i), float(i), [i], None) for i in range(2000)]}
        cyclic = [1, 2]
        cyclic.append(cyclic)
        blob = marshal.dumps(code)

        def run():
            return (len(marshal.dumps(code)), len(marshal.dumps(data_record)),
                    marshal.loads(blob).co_name, len(marshal.loads(marshal.dumps(data_record))["rows"]),
                    len(marshal.loads(marshal.dumps(cyclic))))
        return run
    return ("marshal",), setup


def k_html_parser():
    def setup():
        from html.parser import HTMLParser
        body = "".join(f'<div class="item" id="i{i}"><a href="/x/{i}?a=1&amp;b=2">{t}</a>'
                       f"<!-- note {i} --><br/></div>" for i, t in enumerate(_text(600)))
        document = f"<!DOCTYPE html><html><head><title>t</title></head><body>{body}</body></html>"

        class Counter(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = 0

            def handle_starttag(self, tag, attrs):
                self.tags += 1

        def run():
            parser = Counter()
            parser.feed(document)
            parser.close()
            return parser.tags
        return run
    return ("html.parser",), setup


def k_difflib():
    def setup():
        import difflib
        left = _text(600)
        right = [line if i % 11 else line.upper() for i, line in enumerate(left)]
        right = right[50:] + right[:50]

        def run():
            matcher = difflib.SequenceMatcher(None, left, right)
            diff = list(difflib.unified_diff(left, right, lineterm=""))
            return len(matcher.get_matching_blocks()), round(matcher.ratio(), 6), len(diff)
        return run
    return ("difflib",), setup


def k_codecs():
    def setup():
        import codecs
        text = "\n".join(_text(3000)) + " ü€𝄞" * 200
        data = text.encode("utf-8")

        def run():
            decoder = codecs.getincrementaldecoder("utf-8")()
            pieces = [decoder.decode(data[i:i + 4093]) for i in range(0, len(data), 4093)]
            encoder = codecs.getincrementalencoder("utf-8")()
            encoded = b"".join(encoder.encode(text[i:i + 4093]) for i in range(0, len(text), 4093))
            return len(codecs.encode(text, "utf-8")), len(codecs.decode(data, "utf-8")), len("".join(pieces)), len(encoded)
        return run
    return ("codecs",), setup


def k_unicodedata():
    def setup():
        import unicodedata
        text = ("Ångström café naïve ﬁne Ⅻ ｶﾀｶﾅ ḉ " * 200)
        unicode_18_additions = {0x05C9, 0x0B54, 0x1ADE, 0x1AED, 0x1AF0, 0x209F, 0x20C3, 0x2E61}
        chars = [chr(code) for code in range(0x20, 0x3000, 3) if code not in unicode_18_additions]

        def run():
            categories = [unicodedata.category(ch) for ch in chars]
            combining = sum(unicodedata.combining(ch) for ch in chars)
            forms = [unicodedata.normalize(form, text) for form in ("NFC", "NFD", "NFKC", "NFKD")]
            return categories[-1], combining, [len(form) for form in forms], unicodedata.is_normalized("NFC", text)
        return run
    return ("unicodedata",), setup


def k_bz2():
    def setup():
        import bz2
        data = _blob(256 * 1024)
        packed = bz2.compress(data)

        def run():
            compressor = bz2.BZ2Compressor()
            streamed = compressor.compress(data[:65536]) + compressor.flush()
            decompressor = bz2.BZ2Decompressor()
            restored = decompressor.decompress(packed)
            return (len(bz2.decompress(bz2.compress(data))), len(bz2.decompress(packed)),
                    len(bz2.decompress(streamed)), len(restored))
        return run
    return ("bz2",), setup


def k_lzma():
    def setup():
        import lzma
        data = _blob(256 * 1024)
        packed = lzma.compress(data)

        def run():
            compressor = lzma.LZMACompressor()
            streamed = compressor.compress(data[:65536]) + compressor.flush()
            decompressor = lzma.LZMADecompressor()
            restored = decompressor.decompress(packed)
            return (len(lzma.decompress(lzma.compress(data))), len(lzma.decompress(packed)),
                    len(lzma.decompress(streamed)), len(restored))
        return run
    return ("lzma",), setup


def k_compression_zstd():
    def setup():
        from compression import zstd
        data = _blob(1 << 20)
        packed = zstd.compress(data)

        def run():
            compressor = zstd.ZstdCompressor()
            streamed = compressor.compress(data[:262144]) + compressor.flush()
            decompressor = zstd.ZstdDecompressor()
            restored = decompressor.decompress(packed)
            return (len(zstd.decompress(zstd.compress(data))), len(zstd.decompress(packed)),
                    len(zstd.decompress(streamed)), len(restored))
        return run
    return ("compression.zstd",), setup


def k_zipimport():
    def setup():
        import tempfile
        import zipfile
        import zipimport
        base = tempfile.mkdtemp(prefix="perf-zipimport-")
        path = os.path.join(base, "modules.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for i in range(60):
                archive.writestr(f"zmod{i}.py", f"VALUE = {i}\n" + "# padding\n" * 200)
            archive.writestr("zpkg/__init__.py", "")
            archive.writestr("zpkg/data.txt", _blob(8192))

        def run():
            importer = zipimport.zipimporter(path)
            specs = [importer.find_spec(f"zmod{i}") for i in range(60)]
            sources = [importer.get_source(f"zmod{i}") for i in range(0, 60, 3)]
            return sum(spec is not None for spec in specs), len(sources[-1]), len(importer.get_data("zpkg/data.txt"))
        return run
    return ("zipimport",), setup


def k_glob():
    def setup():
        import glob
        import tempfile
        base = tempfile.mkdtemp(prefix="perf-glob-")
        for i in range(300):
            open(os.path.join(base, f"file_{i}.{('txt', 'py', 'json')[i % 3]}"), "w").close()

        def run():
            return (len(glob.glob(os.path.join(base, "*.py"))), len(glob.glob(os.path.join(base, "file_1?.*"))),
                    len(glob.glob("file_[0-4]*.txt", root_dir=base)))
        return run
    return ("glob",), setup


def k_fnmatch():
    def setup():
        import fnmatch
        names = [f"pkg/{t.replace(' ', '_')}.{('py', 'txt', 'json', 'cfg')[i % 4]}" for i, t in enumerate(_text(2000))]
        patterns = ["*.py", "pkg/alpha*", "*[0-9]*", "*_beta_*.json", "?kg/*.cfg"]

        def run():
            return ([len(fnmatch.filter(names, pattern)) for pattern in patterns],
                    sum(fnmatch.fnmatchcase(name, "pkg/*a*.txt") for name in names))
        return run
    return ("fnmatch",), setup


def k_importlib_resources():
    def setup():
        import importlib.resources

        def run():
            root = importlib.resources.files("email")
            entries = sorted(entry.name for entry in root.iterdir())
            data = root.joinpath("__init__.py").read_bytes()
            nested = importlib.resources.files("email.mime").joinpath("text.py").read_text()
            functional_data = importlib.resources.read_binary("email", "__init__.py")
            functional_text = importlib.resources.read_text("email.mime", "text.py", encoding="utf-8")
            return len(entries), len(data), len(nested), len(functional_data), len(functional_text)
        return run
    return ("importlib.resources",), setup


def k_tempfile():
    def setup():
        import tempfile

        def run():
            created = 0
            for _ in range(100):
                descriptor, name = tempfile.mkstemp(prefix="perf-")
                os.close(descriptor)
                os.unlink(name)
                directory = tempfile.mkdtemp(prefix="perf-")
                os.rmdir(directory)
                created += 2
            return created
        return run
    return ("tempfile",), setup


def k_fractions():
    def setup():
        from fractions import Fraction
        texts = [f"{i}/{i % 97 + 1}" for i in range(1, 2000)]

        def run():
            values = [Fraction(text) for text in texts]
            total = Fraction(0)
            for value in values[:500]:
                total = total + value * Fraction(3, 7) - value / 11
            return str(total)[:40], str(values[-1])
        return run
    return ("fractions",), setup


def k_statistics():
    def setup():
        import statistics
        data = [(i * 7919) % 10007 for i in range(20000)]

        def run():
            return statistics.mean(data), statistics.median(data), statistics.variance(data[:5000])
        return run
    return ("statistics",), setup


def k_random():
    def setup():
        import random
        population = list(range(10000))

        def run():
            generator = random.Random(12345)
            picks = [generator.choice(population) for _ in range(5000)]
            samples = [generator.sample(population, 20) for _ in range(300)]
            return picks[-1], samples[-1]
        return run
    return ("random",), setup


def k_collections():
    def setup():
        from collections import Counter
        words = " ".join(_text(3000)).split()

        def run():
            counter = Counter()
            counter.subtract(words)
            counter.subtract(words[:5000])
            return sorted(counter.items())[:3]
        return run
    return ("collections",), setup


def k_heapq():
    def setup():
        import heapq
        values = [(i * 7919) % 100003 for i in range(20000)]

        def run():
            heap = []
            for value in values:
                heapq.heappush(heap, value)
            smallest = [heapq.heappop(heap) for _ in range(5000)]
            maxheap = values[:10000]
            heapq.heapify_max(maxheap)
            largest = [heapq.heappop_max(maxheap) for _ in range(2000)]
            return smallest[-1], largest[-1]
        return run
    return ("heapq",), setup


def k_bisect():
    def setup():
        import bisect
        values = [(i * 7919) % 100003 for i in range(5000)]

        def run():
            ordered = []
            for value in values:
                bisect.insort(ordered, value)
            return [bisect.bisect_left(ordered, value) for value in values[:3000]][-1], ordered[-1]
        return run
    return ("bisect",), setup


def k_itertools():
    def setup():
        import itertools
        data = list(range(20000))
        mask = [i % 3 == 0 for i in data]

        def run():
            return (sum(itertools.chain(data, data)), sum(itertools.compress(data, mask)),
                    sum(itertools.dropwhile(lambda x: x < 5000, data)),
                    sum(itertools.filterfalse(lambda x: x % 2, data)),
                    sum(itertools.islice(data, 100, 15000, 3)),
                    sum(itertools.starmap(pow, ((x % 10, 2) for x in data[:5000]))),
                    sum(itertools.takewhile(lambda x: x < 15000, data)))
        return run
    return ("itertools",), setup


def k_functools():
    def setup():
        import functools
        values = [(i * 7919) % 10007 for i in range(8000)]

        def compare(left, right):
            return (left > right) - (left < right)

        def run():
            return sorted(values, key=functools.cmp_to_key(compare))[::1000]
        return run
    return ("functools",), setup


def k_contextlib():
    def setup():
        import asyncio
        import contextlib

        @contextlib.contextmanager
        def managed(i, log):
            log.append(i)
            yield i

        @contextlib.asynccontextmanager
        async def amanaged(i, log):
            log.append(i)
            yield i

        async def async_part(log):
            for _ in range(50):
                async with contextlib.AsyncExitStack() as stack:
                    for i in range(10):
                        await stack.enter_async_context(amanaged(i, log))
                        stack.push_async_callback(asyncio.sleep, 0)

        def run():
            log = []
            for _ in range(300):
                with contextlib.ExitStack() as stack:
                    for i in range(10):
                        stack.enter_context(managed(i, log))
                        stack.callback(log.append, -i)
            asyncio.run(async_part(log))
            return len(log)
        return run
    return ("contextlib",), setup


def k_dataclasses():
    def setup():
        import dataclasses

        def run():
            made = []
            for i in range(60):
                cls = dataclasses.make_dataclass(
                    f"Record{i}", [("id", int), ("name", str, dataclasses.field(default="x")),
                                   ("tags", list, dataclasses.field(default_factory=list))],
                    order=True, frozen=bool(i % 2))
                made.append(cls(i))
            return repr(made[-1]), len(dataclasses.fields(made[-1]))
        return run
    return ("dataclasses",), setup


def k_inspect():
    def setup():
        import inspect

        def target(a, b, /, c, *args, d=4, e, **kwargs):
            return a

        signature = inspect.signature(target)

        class Sample:
            x = 1

            def method(self):
                return self.x

        def run():
            bound = [signature.bind(1, 2, 3, 5, 6, e=7, f=8) for _ in range(2000)]
            partial = [signature.bind_partial(1, e=2) for _ in range(2000)]
            members = [inspect.getmembers(Sample) for _ in range(50)]
            return str(bound[-1]), str(partial[-1]), len(members[-1])
        return run
    return ("inspect",), setup


def k_ast():
    def setup():
        import ast
        source = "\n".join(f"def f{i}(x):\n    y = x + {i}\n    return [y * 2 for _ in range(3)]" for i in range(200))
        tree = ast.parse(source)

        class Renamer(ast.NodeTransformer):
            def visit_Name(self, node):
                return ast.copy_location(ast.Name(id=node.id.upper(), ctx=node.ctx), node)

        def run():
            count = sum(1 for _ in ast.walk(tree))
            transformed = ast.fix_missing_locations(Renamer().visit(ast.parse(source)))
            return count, len(ast.dump(transformed))
        return run
    return ("ast",), setup


def k_argparse():
    def setup():
        import argparse
        parser = argparse.ArgumentParser(prog="kernel")
        parser.add_argument("--name", default="x")
        parser.add_argument("--count", type=int, default=1)
        parser.add_argument("-v", "--verbose", action="count", default=0)
        parser.add_argument("--tag", action="append", default=[])
        parser.add_argument("paths", nargs="*")
        argv = ["--name", "alpha", "--count", "3", "-vv", "--tag", "a", "--tag", "b", "p1", "p2", "p3"]

        def run():
            parsed = [parser.parse_args(argv) for _ in range(500)]
            return vars(parsed[-1])
        return run
    return ("argparse",), setup


def k_tokenize():
    def setup():
        import io
        import tokenize
        source = "\n".join(f"def f{i}(x, y={i}):\n    return x + y * {i} # note\n" for i in range(300))
        simple_source = "x = 1 + 2\n" * 64

        def run():
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
            simple_tokens = list(tokenize.generate_tokens(io.StringIO(simple_source).readline))
            return len(tokens), tokens[-3].string, len(simple_tokens), simple_tokens[-3].string
        return run
    return ("tokenize",), setup


def k_strptime():
    def setup():
        import time
        from datetime import datetime
        stamps = [f"2024-{1 + i % 12:02d}-{1 + i % 28:02d} {i % 24:02d}:{i % 60:02d}:{(i * 7) % 60:02d}"
                  for i in range(1500)]

        def run():
            parsed = [datetime.strptime(text, "%Y-%m-%d %H:%M:%S") for text in stamps]
            structs = [time.strptime(text, "%Y-%m-%d %H:%M:%S") for text in stamps[:500]]
            return parsed[-1].isoformat(), tuple(structs[-1])[:6]
        return run
    return ("_strptime", "datetime"), setup


def k_shlex():
    def setup():
        import shlex
        commands = [f"cmd --name='value {i}' \"quoted {t}\" plain\\ escaped -x {i}" for i, t in enumerate(_text(800))]

        def run():
            posix = [shlex.split(command) for command in commands]
            lexer = [list(shlex.shlex(command, posix=False)) for command in commands[:300]]
            return posix[-1], lexer[-1]
        return run
    return ("shlex",), setup


def k_textwrap():
    def setup():
        import textwrap
        paragraphs = [" ".join(_text(12, seed=i)) for i in range(80)]

        def run():
            wrapped = [textwrap.wrap(text, width=60) for text in paragraphs]
            filled = [textwrap.fill(text, width=72) for text in paragraphs]
            short = [textwrap.shorten(text, width=40) for text in paragraphs]
            return len(wrapped[-1]), len(filled[-1]), short[-1]
        return run
    return ("textwrap",), setup


def k_threading():
    def setup():
        import threading
        barrier = threading.Barrier(1)

        def run():
            return sum(barrier.wait() == 0 for _ in range(5000)), barrier.n_waiting
        return run
    return ("threading",), setup


def k_typing():
    def setup():
        import typing
        aliases = [list[int], dict[str, list[int]], typing.Optional[int], typing.Union[int, str],  # noqa: UP045, UP007
                   typing.Callable[[int], str], tuple[int, ...], typing.Literal["a", "b"],
                   typing.Annotated[int, "meta"]] * 400

        def run():
            origins = [typing.get_origin(alias) for alias in aliases]
            arguments = [typing.get_args(alias) for alias in aliases]
            return repr(origins[:8]), repr(arguments[:8])
        return run
    return ("typing",), setup


def k_warnings():
    def setup():
        import io
        import warnings

        def run():
            stream = io.StringIO()
            with warnings.catch_warnings():
                for i in range(200):
                    warnings.filterwarnings("ignore", message=f"pattern {i}", category=UserWarning)
                for i in range(300):
                    warnings.showwarning(UserWarning(f"message {i}"), UserWarning, "kernel.py", i, file=stream)
                text = warnings.formatwarning("formatted", DeprecationWarning, "kernel.py", 1)
            return len(stream.getvalue()), text
        return run
    return ("warnings",), setup


def k_urllib_request():
    def setup():
        import urllib.request
        opener = urllib.request.build_opener()
        urls = [f"data:text/plain;charset=utf-8,item%20{i}" for i in range(300)]

        def run():
            bodies = []
            for url in urls:
                with opener.open(url) as response:
                    bodies.append(response.read())
            return bodies[-1], len(bodies)
        return run
    return ("urllib.request",), setup


KERNELS = {
    "urllib.parse": k_urllib_parse, "json": k_json, "pickle": k_pickle, "csv": k_csv,
    "tomllib": k_tomllib, "email": k_email, "xml.etree.ElementTree": k_xml_etree, "re": k_re,
    "base64": k_base64, "binascii": k_binascii, "zlib": k_zlib, "gzip": k_gzip,
    "zipfile": k_zipfile, "tarfile": k_tarfile, "pathlib": k_pathlib, "os.path": k_os_path,
    "shutil": k_shutil, "importlib.metadata": k_importlib_metadata, "hashlib": k_hashlib,
    "hmac": k_hmac, "uuid": k_uuid, "datetime": k_datetime, "decimal": k_decimal,
    "sqlite3": k_sqlite3, "io": k_io, "logging": k_logging, "asyncio": k_asyncio,
    "http.client": k_http_client, "ipaddress": k_ipaddress, "socket": k_socket, "ssl": k_ssl,
    "subprocess": k_subprocess, "multiprocessing": k_multiprocessing,
    "concurrent.futures": k_concurrent_futures, "configparser": k_configparser,
    "plistlib": k_plistlib, "struct": k_struct, "marshal": k_marshal,
    "html.parser": k_html_parser, "difflib": k_difflib, "codecs": k_codecs,
    "unicodedata": k_unicodedata, "bz2": k_bz2, "lzma": k_lzma,
    "compression.zstd": k_compression_zstd, "zipimport": k_zipimport, "glob": k_glob,
    "fnmatch": k_fnmatch, "importlib.resources": k_importlib_resources, "tempfile": k_tempfile,
    "fractions": k_fractions, "statistics": k_statistics, "random": k_random,
    "collections": k_collections, "heapq": k_heapq, "bisect": k_bisect,
    "itertools": k_itertools, "functools": k_functools, "contextlib": k_contextlib,
    "dataclasses": k_dataclasses, "inspect": k_inspect, "ast": k_ast, "argparse": k_argparse,
    "tokenize": k_tokenize, "_strptime": k_strptime, "shlex": k_shlex, "textwrap": k_textwrap,
    "threading": k_threading, "typing": k_typing, "warnings": k_warnings,
    "urllib.request": k_urllib_request,
}


def measure(route: str, iterations: int) -> dict[str, object]:
    import importlib

    imports, setup = KERNELS[route]()
    before = _usage()
    for name in imports:
        importlib.import_module(name)
    first = setup()
    result = first()
    after_first = _usage()
    # A second setup and call repeats only per-instance inputs and state:
    # imports, lazily loaded extensions, and module-level caches are already
    # resident. Their difference estimates the route's fixed load without
    # counting input construction, identically on both interpreters.
    second = setup()
    second()
    after_second = _usage()
    del first
    digest = format(hash(repr(result)) & ((1 << 64) - 1), "016x")
    _reset_interval_peak()
    start = _usage()
    cpu, wall = time.process_time(), time.perf_counter()
    for _ in range(iterations):
        second()
    cpu, wall = time.process_time() - cpu, time.perf_counter() - wall
    end = _usage()

    def fixed(field: str) -> int:
        once = getattr(after_first, field) - getattr(before, field)
        again = getattr(after_second, field) - getattr(after_first, field)
        return max(0, once - again)

    result = {
        "route": route,
        "iterations": iterations,
        "cpu_seconds_per_iteration": cpu / iterations,
        "wall_seconds_per_iteration": wall / iterations,
        "load_footprint_bytes": fixed("ri_phys_footprint"),
        "load_resident_bytes": fixed("ri_resident_size"),
        "working_peak_bytes": max(0, end.ri_interval_max_phys_footprint - start.ri_phys_footprint),
        "digest": digest,
    }
    # Serialize retained counters after every measured snapshot. Keeping the
    # first and repeated growth separate permits attribution without changing
    # the fixed-footprint estimate or introducing work between snapshots.
    result["raw_memory_counters"] = {
        phase: {
            "ri_phys_footprint": snapshot.ri_phys_footprint,
            "ri_resident_size": snapshot.ri_resident_size,
            "ri_interval_max_phys_footprint": snapshot.ri_interval_max_phys_footprint,
        }
        for phase, snapshot in (("before", before), ("after_first", after_first),
                                ("after_second", after_second), ("loop_start", start),
                                ("loop_end", end))
    }
    return result


def main(argv: list[str]) -> int:
    if len(argv) == 1 and argv[0] == "list":
        import json
        print(json.dumps(sorted(KERNELS)))
        return 0
    if len(argv) != 4 or argv[0] != "measure" or argv[2] != "--iterations":
        print("usage: perf_modules.py list | measure ROUTE --iterations N", file=sys.stderr)
        return 2
    result = measure(argv[1], int(argv[3]))
    result.update(runtime_executable=sys.executable, runtime_base_executable=sys._base_executable,
                  runtime_prefix=sys.prefix, runtime_exec_prefix=sys.exec_prefix)
    import json
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
