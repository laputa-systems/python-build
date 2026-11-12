PYTHON ?= python3

TARGET ?= $(shell $(PYTHON) -c 'from buildsys.targets import native_target; print(native_target().triple)')

BENCH_TARGET ?= $(TARGET)
BENCH_ARTIFACT = $(shell $(PYTHON) -c 'from pathlib import Path; from buildsys.generations import active_generation; from buildsys.product import PRODUCT; root = Path("dist/$(BENCH_TARGET)"); print(active_generation(root) / PRODUCT.local_archive_name("$(BENCH_TARGET)", "r2") if (root / "ACTIVE").exists() else root / "unpublished")')
BENCH_PRODUCT_LABEL := python-build CPython $(shell $(PYTHON) -c 'from buildsys.product import PRODUCT; print(PRODUCT.version_string)')
BENCH_PROFILE ?= standard
BENCH_PYPERFORMANCE_SELECTION ?= all
BENCH_PYPERFORMANCE_ARGS = $(if $(strip $(BENCH_PYPERFORMANCE_SELECTION)),--pyperformance-selection $(BENCH_PYPERFORMANCE_SELECTION),)

.DEFAULT_GOAL := lint

.PHONY: bench bench-full bench-host-check bench-inputs bench-product
.PHONY: fetch build package verify repro lint clean

fetch:
	$(PYTHON) build.py fetch --target $(TARGET)

build: fetch
	$(PYTHON) build.py qualify --target $(TARGET)

package:
	$(PYTHON) build.py package --target $(TARGET)

verify:
	$(PYTHON) build.py verify --target $(TARGET)

repro:
	$(PYTHON) build.py reproduce --target $(TARGET)

lint:
	uv run --frozen ruff check . --exclude .work
	cargo clippy --manifest-path backend/hashing/Cargo.toml --locked --all-targets -- -D warnings

clean:
	rm -rf dist diagnostics build/prefix build/work build/stage build/logs build/sealed
	rm -rf rust-cpython/stage rust-cpython/work rust-cpython/logs rust-cpython/results rust-cpython/stage-perf-*
	cargo clean --manifest-path backend/hashing/Cargo.toml
	rm -rf .venv .pytest_cache .ruff_cache
	find . -path './.git' -prune -o -path './.work' -prune -o -name __pycache__ -type d -prune -exec rm -rf {} +

ifeq ($(BENCH_TARGET),aarch64-apple-darwin)
# The locked wheelhouse is Linux/musl-only; macOS uses dependency-free smoke workloads.
BENCH_SUITE ?= smoke
BENCH_RUN_OPTIONS = --local
BENCH_PREPARE = @:
else
BENCH_SUITE ?= realworld
BENCH_RUN_OPTIONS =
BENCH_PREPARE = $(PYTHON) benchmarks/bench.py prepare
endif

bench: bench-inputs bench-product
	$(PYTHON) benchmarks/bench.py run \
		--preset pbs \
		--candidate "$(BENCH_ARTIFACT)" \
		--candidate-label "$(BENCH_PRODUCT_LABEL)" \
		--candidate-kind python-build \
		--suite $(BENCH_SUITE) \
		--profile $(BENCH_PROFILE) \
		$(BENCH_RUN_OPTIONS)

ifeq ($(BENCH_TARGET),aarch64-apple-darwin)
bench-full:
	@echo "bench-full requires the Linux amd64 benchmark environment" >&2
	@exit 2
else
bench-full: bench-inputs bench-product
	$(PYTHON) benchmarks/bench.py run \
		--preset pbs \
		--candidate "$(BENCH_ARTIFACT)" \
		--candidate-label "$(BENCH_PRODUCT_LABEL)" \
		--candidate-kind python-build \
		--suite full \
		--profile $(BENCH_PROFILE) \
		$(BENCH_PYPERFORMANCE_ARGS)
endif

bench-host-check:
	$(PYTHON) -c 'from buildsys.targets import native_target; actual = native_target().triple; expected = "$(BENCH_TARGET)"; supported = {"x86_64-unknown-linux-musl", "aarch64-apple-darwin"}; print("Benchmark target:", actual); assert actual in supported, f"make bench does not support {actual}"; assert actual == expected, f"make bench requires native {expected}, found {actual}"'

bench-inputs: bench-host-check
	$(PYTHON) benchmarks/bench.py fetch --target $(BENCH_TARGET)
	$(BENCH_PREPARE)

bench-product: bench-host-check
	@if test -f "$(BENCH_ARTIFACT)"; then \
		printf '%s\n' "Using existing product artifact: $(BENCH_ARTIFACT)"; \
	else \
		$(PYTHON) build.py fetch --target $(BENCH_TARGET) && \
		$(PYTHON) build.py build --target $(BENCH_TARGET) && \
		$(PYTHON) build.py package --target $(BENCH_TARGET); \
	fi
