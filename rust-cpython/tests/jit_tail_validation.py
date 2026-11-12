from pathlib import Path
import re
import shlex
import subprocess
import sys
import sysconfig
import tempfile

from test.support import SuppressCrashReport


LANE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LANE))
import perf


def compile_validator(source, directory):
    checker = source[source.index("#define CHECK(PRED)"):source.index("#undef CHECK")]
    terminator = re.search(r"static int\nis_terminator\(.*?\n}\n", source, re.S)
    assert terminator is not None
    program = r'''
#include "Python.h"
#include "opcode.h"
#include "pycore_opcode_metadata.h"
#include "pycore_optimizer.h"
#include "pycore_uop_ids.h"
#define NEED_OPCODE_METADATA
#include "pycore_uop_metadata.h"
#undef NEED_OPCODE_METADATA
#include <assert.h>
#include <stdlib.h>
'''
    program += terminator.group(0) + checker
    program += r'''
int main(int argc, char **argv)
{
    assert(argc == 2);
    int which = atoi(argv[1]);
    const uint16_t tails[] = {
        _ERROR_POP_N,
        _MATERIALIZE_FLOAT_LEAF_ERROR,
        _MATERIALIZE_VIRTUAL_FRAME,
        _LOAD_FAST,
    };
    assert(which >= 0 && which < 4);
    _PyUOpInstruction trace[3] = {0};
    trace[0].opcode = _PyUop_Caching[_START_EXECUTOR].entries[0].opcode;
    trace[0].format = UOP_FORMAT_JUMP;
    trace[0].jump_target = 1;
    trace[0].error_target = 2;
    trace[1].opcode = _PyUop_Caching[_JUMP_TO_TOP].entries[0].opcode;
    trace[1].format = UOP_FORMAT_JUMP;
    trace[1].jump_target = 1;
    trace[2].opcode = _PyUop_Caching[tails[which]].entries[0].opcode;
    trace[2].format = UOP_FORMAT_TARGET;
    _PyExecutorObject executor = {0};
    executor.code_size = 3;
    executor.trace = trace;
    sanity_check(&executor);
    return 0;
}
'''
    paths = perf._paths(Path(sys.executable).resolve().parents[1].name.removeprefix("stage-"))
    original = perf._existing_source(paths["source_parent"])
    compiler = shlex.split(sysconfig.get_config_var("CC"))
    flags = [*shlex.split(sysconfig.get_config_var("CFLAGS") or ""),
             *shlex.split(sysconfig.get_config_var("CPPFLAGS") or "")]
    flags = [flag for flag in flags if not flag.startswith("-DNDEBUG")]
    cfile = directory / "validator.c"
    binary = directory / "validator"
    cfile.write_text(program)
    command = [*compiler, *flags, "-UNDEBUG", "-DPy_BUILD_CORE", "-D_Py_TIER2=1", "-D_Py_JIT",
        "-I" + str(paths["build"]), "-I" + str(original / "Include"),
        "-I" + str(original / "Include/internal"), str(cfile), "-o", str(binary),
        *shlex.split(sysconfig.get_config_var("LDFLAGS") or "")]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return binary


def main():
    scratch = LANE.parent / ".work/tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    source = (LANE / "overlay/Python/optimizer.c").read_text()
    with tempfile.TemporaryDirectory(prefix="tail-validator-", dir=scratch) as directory:
        binary = compile_validator(source, Path(directory))
        with SuppressCrashReport():
            for case in range(4):
                result = subprocess.run([str(binary), str(case)], capture_output=True, text=True, timeout=10)
                if case < 3:
                    assert result.returncode == 0, (case, result.returncode, result.stderr)
                else:
                    assert result.returncode != 0 and "Assertion" in result.stderr, result
    print("OK    assertion-enabled tail validator accepts materializers and rejects ordinary uops")
    return 0


if __name__ == "__main__":
    sys.exit(main())
