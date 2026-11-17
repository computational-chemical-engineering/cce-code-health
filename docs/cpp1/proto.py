"""Throwaway prototype of cpp@1 rules, to calibrate advice. Not production code."""
import json, sys, os, re
from bisect import bisect_left, bisect_right
sys.path.insert(0, '/home/eajfpeters/Code/cce-code-health/src')
from cce_code_health.cpp_backend import _TOKEN, CONTROL, CONTINUATION, _skip_parens
import lizard

COND = {'if', 'for', 'while', 'catch', '&&', '||', 'case', '?'}
LMACROS = {'KOKKOS_LAMBDA': '=', 'KOKKOS_CLASS_LAMBDA': '=this'}
KCALLS = {'parallel_for', 'parallel_reduce', 'parallel_scan'}
LOOPS = {'for', 'while', 'do'}
KW = set('''alignas alignof and asm auto bool break case catch char class const constexpr const_cast
continue decltype default delete do double else enum explicit extern false float for friend goto if
inline int long mutable namespace new noexcept not nullptr operator or private protected public
return short signed sizeof static static_assert static_cast struct switch template this throw true
try typedef typeid typename union unsigned using virtual void volatile while size_t'''.split())


def toks(src):
    out, line, pos = [], 1, 0
    for m in _TOKEN.finditer(src):
        kind, text = m.lastgroup, m.group()
        line += src.count('\n', pos, m.start())
        if kind != 'comment':
            out.append((m.start(), m.end(), line, kind, '0' if kind in ('raw', 'string', 'char', 'number') else text))
        line += text.count('\n')
        pos = m.end()
    return out


def blank(chars, a, b):
    for k in range(a, b):
        if chars[k] != '\n':
            chars[k] = ' '


PPD = re.compile(r'^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)', re.S)


def pp_select(src, tl):
    """Blank conditional directives and all but the max-decision branch of each group."""
    chars = list(src)
    root = {'items': []}
    stack = [root]          # current branch dicts
    gstack = []
    for t in tl:
        if t[3] == 'pp':
            m = PPD.match(t[4])
            if not m:
                continue
            d, rest = m.group(1), m.group(2).strip()
            dead = d in ('if', 'elif') and re.match(r'^(0|false)\b', rest) is not None
            if d in ('if', 'ifdef', 'ifndef'):
                g = {'branches': [], 'dirs': [t]}
                stack[-1]['items'].append(g)
                gstack.append(g)
                br = {'items': [], 'dead': dead, 'start': t[1]}
                g['branches'].append(br); stack.append(br)
            elif d in ('elif', 'else') and gstack:
                g = gstack[-1]; stack[-1]['end'] = t[0]; stack.pop()
                g['dirs'].append(t)
                br = {'items': [], 'dead': dead, 'start': t[1]}
                g['branches'].append(br); stack.append(br)
            elif d == 'endif' and gstack:
                g = gstack.pop(); stack[-1]['end'] = t[0]; stack.pop(); g['dirs'].append(t)
        else:
            stack[-1]['items'].append(t)
    while gstack:
        gstack.pop(); stack[-1]['end'] = len(src); stack.pop()

    def cost(br):
        c = 0
        for it in br['items']:
            if isinstance(it, dict):
                c += choose(it)[1]
            elif it[4] in COND:
                c += 1
        return c

    def delta(br):
        d = 0
        for it in br['items']:
            if isinstance(it, dict):
                live = [b for b in it['branches'] if not b['dead']]
                d += delta(live[0]) if live else 0
            else:
                d += {'{': 1, '}': -1}.get(it[4], 0)
        return d

    def choose(g):
        if 'choice' in g:
            return g['choice']
        live = [b for b in g['branches'] if not b['dead']]
        if not live:
            g['choice'] = (None, 0); return g['choice']
        best = max(live, key=lambda b: (cost(b), -g['branches'].index(b)))
        if delta(best) != delta(live[0]):
            best = live[0]
        g['choice'] = (best, cost(best))
        return g['choice']

    def apply(br):
        for it in br['items']:
            if isinstance(it, dict):
                for d in it['dirs']:
                    blank(chars, d[0], d[1])
                keep = choose(it)[0]
                for b in it['branches']:
                    if b is keep:
                        apply(b)
                    else:
                        blank(chars, b['start'], b['end'])
    apply(root)
    return ''.join(chars)


def unconstexpr(src):
    chars = list(src)
    tl = [t for t in toks(src) if t[3] != 'pp']
    for a, b in zip(tl, tl[1:]):
        if a[4] == 'if' and b[4] == 'constexpr':
            blank(chars, a[0], a[1])
    return ''.join(chars)


def partners(ct):
    st, p, encl = [], {}, [None] * len(ct)
    for i, t in enumerate(ct):
        encl[i] = st[-1] if st else None
        x = t[4]
        if x in '([{' and len(x) == 1:
            st.append(i)
        elif x in ')]}' and len(x) == 1 and st:
            j = st.pop(); p[j] = i; p[i] = j
    return p, encl


def find_kernels(ct):
    """[(intro, cap_open, cap_close, par_open, par_close, body_open, body_close, kind)]"""
    p, encl = partners(ct)
    out = []
    n = len(ct)
    for i, t in enumerate(ct):
        x = t[4]
        cap = None
        if x in LMACROS and i + 1 < n and ct[i + 1][4] == '(':
            po = i + 1; pc = p.get(po)
            kind = 'macro'
        elif x == '[' and 0 < i and i + 1 < n and ct[i + 1][4] != '[' and ct[i - 1][4] in ('(', ','):
            cc = p.get(i)
            if cc is None or cc + 1 >= n:
                continue
            cap = (i, cc)
            e = encl[i]
            if e is None or ct[e][4] != '(' or e == 0 or ct[e - 1][4] not in KCALLS:
                continue
            kind = 'bracket'
            po = cc + 1 if ct[cc + 1][4] == '(' else None
            pc = p.get(po) if po is not None else None
        else:
            continue
        j = (pc + 1) if pc is not None else (cap[1] + 1)
        steps = 0
        while j < n and ct[j][4] != '{' and ct[j][4] not in (';', ')', ',', '}') and steps < 12:
            j += 1; steps += 1
        if j < n and ct[j][4] == '{' and j in p:
            out.append((i, cap, po, pc, j, p[j], kind, x))
    return out


DECL_PREV_PUNCT = {'>', '*', '&', '&&'}
NOT_DECL_PREV = {'return', 'else', 'case', 'goto', 'throw', 'new', 'delete', 'co_return', 'typedef', 'using', 'do'}


def declared(ct, a, b):
    names = set()
    for k in range(max(a, 1), min(b, len(ct) - 1)):
        t = ct[k][4]
        if not re.match(r'[A-Za-z_]\w*$', t) or t in KW:
            continue
        prev, nxt = ct[k - 1][4], ct[k + 1][4]
        if nxt not in ('=', ';', '(', '{', ',', ':', '[', ')'):
            continue
        if prev in ('*', '&') and nxt == '(':
            continue
        if prev in DECL_PREV_PUNCT or (re.match(r'[A-Za-z_]\w*$', prev) and prev not in NOT_DECL_PREV):
            names.add(t)
    return names


def nest(tk):
    """nesting_depth with the loop credit: (raw, credited)."""
    stack = []
    best = bestc = bestl = 0
    i, n, after_do = 0, len(tk), False

    def close_statement():
        was_do = False
        while stack and stack[-1][0] == 'C':
            was_do = stack.pop()[1]
        return was_do
    while i < n:
        t = tk[i]
        if t == 'while' and after_do:
            after_do = False; i = _skip_parens(tk, i + 1); continue
        after_do = False
        if t in CONTROL or t in CONTINUATION:
            kw = t
            if i + 1 < n and (t, tk[i + 1]) in (('else', 'if'), ('if', 'constexpr')):
                i += 1
            i = _skip_parens(tk, i + 1)
            braced = i < n and tk[i] == '{'
            stack.append(('C{' if braced else 'C', t == 'do', kw))
            ctl = [e for e in stack if e[0] != '{']
            d = len(ctl); loops = sum(e[2] in LOOPS for e in ctl)
            best = max(best, d); bestc = max(bestc, d - (loops >= 2)); bestl = max(bestl, d - (loops == d))
            i += braced; continue
        if t == '{':
            stack.append(('{', False, None))
        elif t == '}':
            close_statement()
            kind, was_do, _ = stack.pop() if stack else ('{', False, None)
            if kind == 'C{':
                after_do = close_statement() or was_do
        elif t == ';':
            after_do = close_statement()
        i += 1
    return best, (bestc, bestl)


def liz(path, text):
    return lizard.FileAnalyzer(lizard.get_extensions([])).analyze_source_code(path + '.cpp', text).function_list


def analyze(path, src, pp=True, cx=True, split=True):
    text = src
    if pp:
        text = pp_select(text, toks(text))
    if cx:
        text = unconstexpr(text)
    tl = toks(text)
    ct = [t for t in tl if t[3] != 'pp']
    lines_of = [t[2] for t in ct]
    ks = find_kernels(ct) if split else []
    chars = list(text)
    for k in ks:
        bo, bc = k[4], k[5]
        blank(chars, ct[bo][1], ct[bc][0])
    parent_text = ''.join(chars)
    funcs = []
    pfl = liz(path, parent_text)
    ptl = [t for t in toks(parent_text) if t[3] != 'pp']
    plines = [t[2] for t in ptl]
    for f in pfl:
        lo, hi = bisect_left(plines, f.start_line), bisect_right(plines, f.end_line)
        raw, cred = nest([t[4] for t in ptl[lo:hi]])
        funcs.append(dict(name=f.name, line=f.start_line, end=f.end_line, nloc=f.nloc, ccn=f.cyclomatic_complexity,
                          params=f.parameter_count, nesting=raw, nesting_c=cred[0], nesting_l=cred[1], captures=0, kind='fn',
                          fparams=list(f.parameters)))
    for idx, k in enumerate(ks):
        intro, cap, po, pc, bo, bc, kind, x = k
        inner = [kk for kk in ks if kk is not k and bo < kk[0] and kk[5] < bc]
        cs = list(text)
        for kk in inner:
            blank(cs, ct[kk[4]][1], ct[kk[5]][0])
        ptxt = text[ct[po][0]:ct[pc][1]] if po is not None else '()'
        body = ''.join(cs[ct[bo][0]:ct[bc][1]])
        syn = '\n' * (ct[intro][2] - 1) + 'void L' + ptxt + ' ' + body
        fl = liz(path, syn)
        if not fl:
            continue
        lf = fl[0]
        btoks = [t[4] for t in toks(body) if t[3] != 'pp']
        raw, cred = nest(btoks[1:-1])
        # captures
        enclosing = next((f for f in pfl if f.start_line <= ct[intro][2] <= f.end_line), None)
        lp = set(lf.parameters)
        if enclosing is not None:
            elo = bisect_left(lines_of, enclosing.start_line)
            names = set(enclosing.parameters) | declared(ct, elo, intro)
        else:
            names = set()
        bd = declared(ct, bo, bc)
        used = set()
        for kk in range(bo + 1, bc):
            t = ct[kk][4]
            if t in names and t not in lp and t not in bd and ct[kk - 1][4] not in ('.', '->', '::') and ct[kk + 1][4] != '::':
                used.add(t)
        explicit = 0
        default = kind == 'macro'
        if cap:
            entries = [c for c in ''.join(t[4] + ' ' for t in ct[cap[0] + 1:cap[1]]).split(',') if c.strip()]
            for e in entries:
                if e.strip() in ('&', '='):
                    default = True
                else:
                    explicit += 1
        captures = (len(used) if default else 0) + explicit + (1 if x == 'KOKKOS_CLASS_LAMBDA' else 0)
        pname = enclosing.name if enclosing is not None else '?'
        funcs.append(dict(name=f'{pname}::lambda#{idx}', line=ct[intro][2], end=ct[bc][2], nloc=lf.nloc,
                          ccn=lf.cyclomatic_complexity, params=lf.parameter_count, nesting=raw, nesting_c=cred[0], nesting_l=cred[1],
                          captures=captures, kind=kind))
    return funcs


def debt(f, th, w, loopcredit, capw):
    nst = f['nesting_c'] if loopcredit else f['nesting']
    d = (w['ccn'] * max(0, f['ccn'] - th['ccn']) + w['nloc'] * max(0, f['nloc'] - th['nloc'])
         + w['nesting'] * max(0, nst - th['nesting']) + w['params'] * max(0, f['params'] - th['params']))
    if capw:
        d += capw[1] * max(0, f['params'] + f['captures'] - capw[0]) if f['kind'] != 'fn' else 0
    return d


if __name__ == '__main__':
    root = sys.argv[1]
    th = dict(ccn=10, nloc=70, nesting=3, params=5); w = dict(ccn=1, nloc=0.1, nesting=3, params=1)
    out = {}
    for repo in sorted(os.listdir(root)):
        if not repo.startswith('peclet-'):
            continue
        name = repo[7:]
        js = json.load(open(os.path.join(root, f'cpp0-{name}.json')))
        files = [p for p in js['files'] if not p.endswith('.py')]
        res = {}
        for p in files:
            src = open(os.path.join(root, repo, p), encoding='utf-8', errors='replace').read()
            res[p] = {v: analyze(p, src, *flags) for v, flags in
                      (('base', (False, False, False)), ('pp', (True, True, False)), ('split', (True, True, True)))}
        out[name] = res
    json.dump(out, open(sys.argv[2], 'w'))
