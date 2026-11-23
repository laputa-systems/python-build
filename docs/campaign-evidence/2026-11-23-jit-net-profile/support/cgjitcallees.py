import lzma,re,sys,collections
names={};calls=collections.Counter();incl=collections.Counter()
def nm(s,tab):
    m=re.match(r'\((\d+)\)(?: (.*))?',s)
    if not m: return s
    if m.group(2): tab[m.group(1)]=m.group(2)
    return tab[m.group(1)]
cur=cfn=None;pending=False
for line in lzma.open(sys.argv[1],'rt',errors='replace'):
    line=line.rstrip('\n')
    if line.startswith('fn='): cur=nm(line[3:],names);continue
    if line.startswith('cfn='): cfn=nm(line[4:],names);continue
    if line.startswith('calls='): c=int(line[6:].split()[0]);pending=True;continue
    if pending:
        pending=False
        if cur and re.match(r'0x00000000060|0x00000000065|0x00000000066|0x0000000006a', cur) and not re.match(r'0x0000000006[0-9a-f]{3}000$', cfn or ''):
            p=line.split(); calls[cfn]+=c; incl[cfn]+=int(p[1]) if len(p)>1 else 0
tot=sum(incl.values());print("total inclusive under JIT callees",f"{tot:,}")
for f,v in incl.most_common(25): print(f"{v:>14,} {calls[f]:>11,} {v/calls[f]:6.1f}/call {f}")
