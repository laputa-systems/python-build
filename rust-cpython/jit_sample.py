import collections
import dataclasses
import re


@dataclasses.dataclass(frozen=True)
class Uop:
    start: int
    end: int
    index: int
    name: str


@dataclasses.dataclass
class Trace:
    start: int
    end: int
    executor: int
    index: int
    offset: int
    name: str
    filename: str
    uops: list[Uop]


@dataclasses.dataclass(frozen=True)
class Attribution:
    total_samples: int
    jit_samples: int
    named_samples: int
    unresolved_samples: int
    unknown_samples: int
    uops: dict[tuple[str, int, int, int, str], int]
    runtime: dict[str, int]


def parse_map(text: str, pid: int) -> list[Trace]:
    traces = []
    current = None
    expected = 0
    for line in text.splitlines():
        fields = line.split('\t')
        if len(fields) < 2:
            raise ValueError('malformed JIT map record')
        record_pid = int(fields[1])
        if record_pid != pid:
            continue
        if fields[0] == 'T' and len(fields) == 10 and current is None:
            start, end = int(fields[2], 16), int(fields[3], 16)
            name = fields[7].encode('ascii').decode('unicode_escape')
            filename = fields[8].encode('ascii').decode('unicode_escape')
            if start >= end or not isinstance(name, str) or not isinstance(filename, str):
                raise ValueError('invalid JIT trace')
            current = Trace(start, end, int(fields[4]), int(fields[5]), int(fields[6]),
                            name, filename, [])
            expected = int(fields[9])
            if expected <= 0:
                raise ValueError('invalid JIT uop count')
            traces.append(current)
        elif fields[0] == 'U' and len(fields) == 6 and current is not None:
            start, end = int(fields[2], 16), int(fields[3], 16)
            index = int(fields[4])
            if not current.start <= start <= end <= current.end or index != len(current.uops):
                raise ValueError('invalid JIT uop range')
            previous_end = current.uops[-1].end if current.uops else current.start
            if start != previous_end:
                raise ValueError('noncontiguous JIT uop range')
            current.uops.append(Uop(start, end, index, fields[5]))
        elif fields[0] == 'Z' and len(fields) == 3 and current is not None:
            if int(fields[2]) != current.executor or len(current.uops) != expected:
                raise ValueError('incomplete JIT trace')
            current = None
        else:
            raise ValueError('malformed JIT map record')
    if current is not None:
        raise ValueError('incomplete JIT trace')
    if not traces:
        raise ValueError(f'JIT map has no traces for process {pid}')
    return traces


def _sample_nodes(text: str):
    header = re.search(r'^Analysis of sampling .* \(pid (\d+)\)', text, re.MULTILINE)
    if header is None:
        raise ValueError('sample report has no process id')
    try:
        graph = text.split('Call graph:\n', 1)[1].split('\nTotal number in stack', 1)[0]
        graph = graph.split('\nSort by top of stack', 1)[0]
    except IndexError as error:
        raise ValueError('sample report has no call graph') from error
    stack = []
    nodes = []
    for line in graph.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([ +!:|]*)(\d+)\s+(.+)', line)
        if match is None:
            raise ValueError(f'unrecognized sample call graph line: {line}')
        depth, count, label = len(match[1]), int(match[2]), match[3]
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1][1] -= count
            if stack[-1][1][1] < 0:
                raise ValueError('sample child counts exceed parent')
        node = [label, count]
        nodes.append(node)
        stack.append((depth, node))
    if not nodes:
        raise ValueError('sample report has an empty call graph')
    return int(header[1]), nodes


def attribute(sample: str, mappings: str) -> Attribution:
    pid, nodes = _sample_nodes(sample)
    traces = parse_map(mappings, pid)
    uops = collections.Counter()
    runtime = collections.Counter()
    total = jit = named = unknown = 0
    for label, count in nodes:
        if count == 0:
            continue
        total += count
        match = re.search(r'\?\?\?\s+\(in <unknown binary>\)\s+\[(0x[0-9a-fA-F]+)\]', label)
        if match:
            address = int(match[1], 16)
            containing = [trace for trace in traces if trace.start <= address < trace.end]
            if not containing:
                unknown += count
                continue
            jit += count
            if len(containing) == 1:
                trace = containing[0]
                matches = [uop for uop in trace.uops if uop.start <= address < uop.end]
                if len(matches) == 1:
                    uop = matches[0]
                    uops[(trace.name, trace.offset, trace.executor, trace.index, uop.name)] += count
                    named += count
        else:
            symbol = label.split('  (in ', 1)[0]
            runtime[symbol] += count
    return Attribution(total, jit, named, jit - named, unknown, dict(uops), dict(runtime))


def render(result: Attribution) -> str:
    uop_totals = collections.Counter()
    for (_, _, _, _, name), count in result.uops.items():
        uop_totals[re.sub(r'_r[0-3][0-3]$', '', name)] += count
    coverage = 100 * result.named_samples / result.jit_samples if result.jit_samples else 0
    lines = [
        (f'SAMPLES total={result.total_samples} jit={result.jit_samples} '
         f'named={result.named_samples} unresolved={result.unresolved_samples} '
         f'unknown={result.unknown_samples} coverage={coverage:.1f}%'),
        'UOPS',
    ]
    for name, count in uop_totals.most_common():
        lines.append(f'{count:7d} {100 * count / result.total_samples:6.2f}% {name}')
    lines.append('RUNTIME')
    for name, count in sorted(result.runtime.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f'{count:7d} {100 * count / result.total_samples:6.2f}% {name}')
    lines.append('TRACES')
    for key, count in sorted(result.uops.items(), key=lambda item: (-item[1], item[0])):
        name, offset, executor, index, uop = key
        lines.append(f'{count:7d} {name}:{offset} executor={executor} index={index} {uop}')
    return '\n'.join(lines)
