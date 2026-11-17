import json, sys
from collections import defaultdict
S = sys.argv[1]
def rankdata(xs):
    o=sorted(range(len(xs)),key=lambda i:xs[i]); r=[0.0]*len(xs); i=0
    while i<len(o):
        j=i
        while j+1<len(o) and xs[o[j+1]]==xs[o[i]]: j+=1
        for k in range(i,j+1): r[o[k]]=(i+j)/2
        i=j+1
    return r
def spear(a,b):
    ra,rb=rankdata(a),rankdata(b); n=len(a); ma=sum(ra)/n; mb=sum(rb)/n
    c=sum((x-ma)*(y-mb) for x,y in zip(ra,rb)); return c/((sum((x-ma)**2 for x in ra)*sum((y-mb)**2 for y in rb))**0.5)
T0=dict(cognitive=15,nloc=70,nesting=3,bumps=2,params=5,boolops=2); W0=dict(cognitive=1.0,nloc=0.1,nesting=3.0,bumps=2.0,params=1.0,boolops=1.0)
def debt(fs,t,w,bump,bo):
    return sum(sum(w[m]*max(0,dict(cognitive=f["cog"],nloc=f["nloc"],nesting=f["nest"],bumps=f[bump],params=f["params"],boolops=f[bo])[m]-t[m]) for m in t) for f in fs)
def evalset(funcs,classes,clones,score,areas):
    by=defaultdict(list); st=defaultdict(int)
    for f in funcs: by[f["path"]].append(f)
    for c in classes: st[c["path"]]+=c["write_pairs"]
    def run(t,w,bump,bo,ws,wcl):
        out=[]
        for a in areas:
            ps=[p for p in score if a=="" or p.startswith(a+"/")]
            ds=[debt(by.get(p,[]),t,w,bump,bo)+ws*st[p]+wcl*clones.get(p,0) for p in ps]
            out.append(spear([score[p] for p in ps],[-x for x in ds]))
        return out
    return run
d=json.load(open(f"{S}/proto/ofs.json"))
cs={f["path"]:f["code_health"] for f in json.load(open("/home/eajfpeters/Code/cce-code-health/baselines/openflowsheet-a9e733e4/codescene_files.json")) if f["language"]=="Python" and isinstance(f["code_health"],(int,float))}
run=evalset(d["funcs"],d["classes"],json.load(open(f"{S}/proto/ofs_clones.json")),cs,("tests","scripts","docs","benchmarks","src"))
steps=[("python@0",T0,W0,"b0","boolops",0,0)]
t=dict(T0)
for label,ch in [("+cog>9",dict(cognitive=9)),("+params>4",dict(params=4)),("+boolops if/while >1",dict(boolops=1)),("+bumps runs >1",dict(bumps=1))]:
    t=dict(t,**ch); steps.append((label,dict(t),W0,"runh_nontry" if t["bumps"]==1 else "b0","bo_ifw" if t["boolops"]==1 else "boolops",0,0))
steps.append(("+state w0.5",t,W0,"runh_nontry","bo_ifw",0.5,0))
steps.append(("+clones w0.1/line",t,W0,"runh_nontry","bo_ifw",0.5,0.1))
print(f"{'cumulative':24} tests scripts docs bench | src")
for s in steps:
    r=run(*s[1:]); print(f"{s[0]:24} "+" ".join(f"{x:.3f}" for x in r[:4])+f" | {r[4]:.3f}")
p=json.load(open(f"{S}/proto/pymrm.json"))
sc={"solve":4.70,"coupling":6.14,"convect":6.94,"numjac":5.30,"ibm":7.46,"operators":8.47,"ibm_recon":8.52,"particles":8.62,"interpolate":8.78,"checks":9.02,"segmentation":9.20,"ibm_coupling":9.24,"grid":9.36,"helpers":9.68}
sc={f"src/pymrm/{k}.py":v for k,v in sc.items()}
pcl={"src/pymrm/coupling.py":56,"src/pymrm/particles.py":16,"src/pymrm/solve.py":17,"src/pymrm/ibm.py":8}
runp=evalset(p["funcs"],p["classes"],pcl,sc,("",))
print("pymrm 14 files: python@0 %.3f" % runp(T0,W0,"b0","boolops",0,0)[0], " python@1 %.3f" % runp(t,W0,"runh_nontry","bo_ifw",0.5,0.1)[0],
      " python@1 without state/clones %.3f" % runp(t,W0,"runh_nontry","bo_ifw",0,0)[0])
