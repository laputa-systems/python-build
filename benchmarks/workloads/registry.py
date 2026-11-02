"""Explicit workload contract used by the benchmark controller.

Each iteration represents the named operation, not an arbitrary loop count.
The driver prints a correctness digest and operation count for every pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Workload:
    name: str
    category: str
    module: str
    operation: str
    iterations: int
    allocation_iterations: int
    packages: tuple[str, ...] = ()
    noise_class: str = "stable"
    timing_boundary: Literal["internal", "process"] = "internal"
    cpu_boundary: Literal["internal", "process"] = "process"


WORKLOADS: tuple[Workload, ...] = (
    Workload("django_wsgi_first_request", "web", "django", "first request process", 1, 1,
             ("Django",), timing_boundary="process"),
    Workload("django_wsgi_request", "web", "django", "request", 30, 5, ("Django",)),
    Workload("django_asgi_request", "web", "django", "request", 30, 5, ("Django",), "noisy"),
    Workload("django_orm_10k", "database", "django", "row", 1, 1, ("Django",)),
    Workload("django_template_realistic", "web", "django", "render", 30, 5, ("Django",)),
    Workload("pylint_source", "tooling", "tooling", "lint pass", 1, 1, ("pylint",)),
    Workload("pycparser_source", "parsing", "tooling", "parse pass", 1, 1, ("pycparser",)),
    Workload("compileall_source", "tooling", "tooling", "compiled source tree", 1, 1),
    Workload("python_startup", "startup", "extra", "process", 1, 1,
             timing_boundary="process"),
    Workload("import_django", "startup", "extra", "import process", 1, 1, ("Django",)),
    Workload("import_app_stack", "startup", "extra", "import process", 1, 1,
             ("Django", "FastAPI", "Pydantic", "SQLAlchemy")),
    Workload("pip_install_wheelhouse", "packaging", "extra", "installation", 1, 1, ("pip",), "noisy"),
    Workload("rust_base64_small", "encoding", "rust_base64", "64-byte Base64 encode", 100_000, 1),
    Workload("rust_base64_large", "encoding", "rust_base64", "1-MiB Base64 encode", 16, 1),
    Workload("zlib_decode_1m", "compression", "zlib", "decoded byte", 16, 1),
    Workload("zlib_stream_4k", "compression", "zlib", "decoded byte", 8, 1),
    Workload("gzip_extract_1m", "compression", "zlib", "extracted byte", 16, 1),
    Workload("zip_read_wheel", "packaging", "zlib", "extracted byte", 4, 1),
    Workload("zipimport_cold", "startup", "zlib", "import process", 3, 1,
             noise_class="noisy"),
    Workload("difflib_unified_mostly_equal", "tooling", "difflib",
             "complete unified diff", 500, 10),
    Workload("difflib_unified_reordered", "tooling", "difflib",
             "complete unified diff", 1000, 10),
    Workload("catalog_url_normalize", "web", "catalog_url", "catalog URL/key batch", 1500, 2),
    Workload("catalog_search_form", "web", "catalog_url_breadth", "48 search form requests and parses", 500, 2),
    Workload("catalog_request_path", "web", "catalog_url_breadth", "48 canonical request paths", 1000, 2),
    Workload("catalog_json_export", "serialization", "catalog_json", "complete 512-record catalog export", 100, 2),
    Workload("catalog_toml_load", "serialization", "catalog_toml", "complete 512-record catalog load", 100, 2),
    Workload("serialization_roundtrip", "serialization", "extra", "roundtrip", 100, 20),
    Workload("multiprocess_pool", "multiprocess", "extra", "pool task", 20, 2, noise_class="noisy"),
    Workload("nbody", "interpreter", "jit_kernels", "20,000-step n-body advance", 1, 1, cpu_boundary="internal"),
    Workload("spectral_norm", "interpreter", "jit_kernels", "spectral-norm solve", 1, 1, cpu_boundary="internal"),
    Workload("float", "interpreter", "jit_kernels", "100,000-point normalize and maximize", 1, 1, cpu_boundary="internal"),
    Workload("richards", "interpreter", "jit_kernels", "scheduler run", 1, 1, cpu_boundary="internal"),
    Workload("raytrace", "interpreter", "jit_kernels", "100x100 frame", 1, 1, cpu_boundary="internal"),
    Workload("fannkuch", "interpreter", "jit_kernels", "9-element fannkuch solve", 1, 1, cpu_boundary="internal"),
    Workload("deltablue", "interpreter", "jit_kernels", "constraint plan execution", 20, 1, cpu_boundary="internal"),
    Workload("chaos", "interpreter", "jit_kernels", "5,000-point frame", 1, 1, cpu_boundary="internal"),
    Workload("go", "interpreter", "jit_kernels", "200-game search", 1, 1, cpu_boundary="internal"),
    Workload("nqueens", "interpreter", "jit_kernels", "8-queens solve", 1, 1, cpu_boundary="internal"),
    Workload("hexiom", "interpreter", "jit_kernels", "level-30 solve", 1, 1, cpu_boundary="internal"),
    Workload("scimark_sor", "interpreter", "jit_kernels", "successive over-relaxation sweep", 10, 1, cpu_boundary="internal"),
    Workload("scimark_sparse_mat_mult", "interpreter", "jit_kernels", "sparse matrix row", 100, 1, cpu_boundary="internal"),
    Workload("scimark_fft", "interpreter", "jit_kernels", "forward and inverse transform", 50, 1, cpu_boundary="internal"),
    Workload("scimark_lu", "interpreter", "jit_kernels", "LU factorization", 5, 1, cpu_boundary="internal"),
    Workload("scimark_monte_carlo", "interpreter", "jit_kernels", "Monte Carlo sample", 100_000, 1, cpu_boundary="internal"),
    Workload("barnes_hut", "interpreter", "jit_kernels", "200-particle quadtree step", 20, 1, cpu_boundary="internal"),
    Workload("pidigits", "interpreter", "jit_kernels", "2,000-digit pi expansion", 1, 1, cpu_boundary="internal"),
    Workload("pyflate", "interpreter", "jit_kernels", "bzip2 archive decode", 1, 1, cpu_boundary="internal"),
    Workload("coroutines", "interpreter", "jit_kernels", "recursive fibonacci(25) coroutine", 10, 1, cpu_boundary="internal"),
    Workload("generators", "interpreter", "jit_kernels", "100,000-node tree traversal", 8, 1, cpu_boundary="internal"),
    Workload("comprehensions", "interpreter", "jit_kernels", "widget tray build", 15_000, 1, cpu_boundary="internal"),
    Workload("richards_super", "interpreter", "jit_kernels", "scheduler run", 5, 1, cpu_boundary="internal"),
    Workload("meteor_contest", "interpreter", "jit_kernels", "60-solution board solve", 3, 1, cpu_boundary="internal"),
)

JIT_SET: tuple[str, ...] = (
    "nbody",
    "spectral_norm",
    "float",
    "richards",
    "raytrace",
    "fannkuch",
    "deltablue",
    "chaos",
    "go",
    "nqueens",
    "hexiom",
    "scimark_sor",
    "scimark_sparse_mat_mult",
    "scimark_fft",
    "scimark_lu",
    "scimark_monte_carlo",
)

JIT_HELDOUT: tuple[str, ...] = (
    "barnes_hut",
    "pidigits",
    "pyflate",
    "coroutines",
    "generators",
    "comprehensions",
    "richards_super",
    "meteor_contest",
)

JIT_KERNELS: tuple[str, ...] = (*JIT_SET, *JIT_HELDOUT)

BY_NAME = {workload.name: workload for workload in WORKLOADS}


def select_workloads(suite: str, profile: str, name: str | None, category: str | None) -> list[Workload]:
    if name is not None and name not in BY_NAME:
        raise ValueError(f"unknown workload: {name}")
    if profile not in {"quick", "standard", "rigorous"}:
        raise ValueError(f"unknown profile: {profile}")
    if suite == "smoke":
        selected = [BY_NAME[n] for n in (
            "python_startup",
            "rust_base64_small",
            "rust_base64_large",
            "serialization_roundtrip",
            "multiprocess_pool",
        )]
    elif suite in {"realworld", "full"}:
        selected = [w for w in WORKLOADS if name is not None or w.name not in JIT_KERNELS]
        if profile == "quick" and name is None and category is None:
            quick = {"django_wsgi_request", "django_orm_10k", "pycparser_source", "python_startup", "serialization_roundtrip"}
            selected = [w for w in selected if w.name in quick]
    elif suite == "pyperformance":
        selected = []
    else:
        raise ValueError(f"unknown suite: {suite}")
    if name is not None:
        selected = [w for w in selected if w.name == name]
    if category is not None:
        selected = [w for w in selected if w.category == category]
    if not selected and suite != "pyperformance":
        raise ValueError("no workloads selected")
    return selected
