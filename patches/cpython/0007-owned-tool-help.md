# Owned-tool help footer

- **Upstream source/version**: `Python/initconfig.c` and
  `Lib/test/test_cmd_line.py` in verified CPython 3.15.1
  (`Python-3.15.1.tar.xz`, SHA-256
  `673ae7232d84c894729882c8257d300ef2d26523911bfd3917ec8ce7f526f99b`).
- **Origin / license**: project-authored; Python Software Foundation License
  Version 2, matching the modified upstream files.
- **Explanation**: append the maintained producer and owned-tool procedure to
  the normal usage help so users and agents can find maintenance instructions.
- **Scope**: normal help output, including the normal-help section of
  `--help-all`; version and interactive banner paths are unchanged.
- **Applicability check**: apply with `patch -p1 --fuzz=0 --dry-run` to the
  verified CPython 3.15.1 source after preceding patches.
- **Regression test**: `test_cmd_line.test_python_build_help_and_version`
  checks the built interpreter's help and short version output; run upstream
  `test_cmd_line` against the patched interpreter.
