from __future__ import annotations

import struct
import sys
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
ELF64_LITTLE_ENDIAN = b"\x02\x01"
PT_GNU_STACK = 0x6474E551
THREAD_STACK_SIZE = 8 << 20


def patch(path: Path) -> bool:
    data = bytearray(path.read_bytes())
    if data[:4] != ELF_MAGIC or data[4:6] != ELF64_LITTLE_ENDIAN:
        return False
    program_offset, = struct.unpack_from("<Q", data, 0x20)
    entry_size, entries = struct.unpack_from("<HH", data, 0x36)
    for index in range(entries):
        offset = program_offset + index * entry_size
        if struct.unpack_from("<I", data, offset)[0] == PT_GNU_STACK:
            struct.pack_into("<Q", data, offset + 40, THREAD_STACK_SIZE)
            path.write_bytes(data)
            return True
    return False


def main(argv: list[str]) -> int:
    for argument in argv:
        for path in sorted(Path(argument).iterdir()):
            if path.is_file():
                patch(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
