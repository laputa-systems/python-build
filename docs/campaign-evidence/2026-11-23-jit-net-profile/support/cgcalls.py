import lzma,re,sys,collections
names={};calls=collections.Counter();selfir=collections.Counter()
def nm(s):
    m=re.match(r'\((\d+)\)(?: (.*))?',s)
    if not m: return s
    if m.group(2): names[m.group(1)]=m.group(2)
    return names[m.group(1)]
cur=None;cfn=None;pending=False
for line in lzma.open(sys.argv[1],'rt',errors='replace'):
    line=line.rstrip('\n')
    if line.startswith('fn='): cur=nm(line[3:]);continue
    if line.startswith('cfn='): cfn=nm(line[4:]);continue
    if line.startswith('calls='): calls[cfn]+=int(line[6:].split()[0]);pending=True;continue
    if pending: pending=False;continue
    if line and (line[0].isdigit() or line[0] in '+-*'):
        p=line.split()
        if len(p)>=2: selfir[cur]+=int(p[1])
for f in sys.argv[2:]:
    c=calls[f];s=selfir[f];print(f"{f:32s} calls {c:>12,} selfIr {s:>14,} per {s/c if c else 0:6.1f}")
