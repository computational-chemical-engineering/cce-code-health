import json, sys, statistics as st
sys.path.insert(0,'.')
import importlib.util
spec=importlib.util.spec_from_file_location('proto','proto.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
out=json.load(open('out.json'))
th=dict(ccn=10,nloc=70,nesting=3,params=5); w=dict(ccn=1,nloc=0.1,nesting=3,params=1)
P='/tmp/claude-1000/-home-eajfpeters-Code/6acb3080-ac42-4dda-bdfb-676e2b649813/scratchpad/peclet/'
def brain(f): return f['ccn']>25 and f['nloc']>70
tot={}
caps=[]; lam_kinds={}
rows=[]
for repo,res in out.items():
    js=json.load(open(P+f'cpp0-{repo}.json'))
    jd=sum(r['debt'] for p,r in js['files'].items() if not p.endswith('.py'))
    jb=sum(f['brain'] for p,r in js['files'].items() if not p.endswith('.py') for f in r['functions'])
    r={}
    for v in ('base','pp','split'):
        fs=[f for p in res for f in res[p][v]]
        r[v]=(sum(m.debt(f,th,w,False,None) for f in fs), sum(brain(f) for f in fs), len(fs))
    fs=[f for p in res for f in res[p]['split']]
    r['split+loop']=(sum(m.debt(f,th,w,True,None) for f in fs), sum(brain(f) for f in fs), len(fs))
    r['+cap8']=(sum(m.debt(f,th,w,True,(8,0.5)) for f in fs),)
    r['+cap5']=(sum(m.debt(f,th,w,True,(5,1.0)) for f in fs),)
    lam=[f for f in fs if f['kind']!='fn']
    kd=sum(m.debt(f,th,w,True,None) for f in lam)
    for f in lam:
        caps.append(f['captures']); lam_kinds[f['kind']]=lam_kinds.get(f['kind'],0)+1
    rows.append((repo, round(jd,1), jb, *[ (round(r[v][0],1), r[v][1]) if len(r[v])>1 else round(r[v][0],1) for v in ('base','pp','split','split+loop','+cap8','+cap5')], len(lam), round(kd,1)))
    for k,v in r.items(): tot.setdefault(k,[0,0]); tot[k][0]+=v[0]; tot[k][1]+= v[1] if len(v)>1 else 0
print('repo json(debt,brain) base pp split split+loop +cap8 +cap5 nlambdas lambda_debt')
for x in rows: print(*x)
print('TOTAL', {k:(round(a,1),b) for k,(a,b) in tot.items()})
print('lambda kinds', lam_kinds, 'captures median', st.median(caps), 'p90', sorted(caps)[int(.9*len(caps))], 'max', max(caps), '>5:', sum(c>5 for c in caps), '>8:', sum(c>8 for c in caps))
