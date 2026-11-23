import concurrent.futures
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_bytes()


def compile_batch(worker):
    for iteration in range(200):
        compile(source, f"thread-{worker}-{iteration}.py", "exec", dont_inherit=True)


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(compile_batch, range(4)))

print("threads=4 iterations_per_thread=200 compiles=800")
