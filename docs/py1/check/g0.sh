#!/bin/bash
# usage: run.sh OUTDIR  -> python@0 G0 outputs
D=$1; mkdir -p $D; cd $D; CH=~/Code/cce-code-health-py1/.venv/bin/codehealth
$CH scan --repo ~/Code/OpenFlowsheet --ref a9e733e4 --json OFS.json --group-depth 2 > OFS.out 2>&1
$CH scan --repo ~/Code/pymrm_suite/pymrm --ref 13176bb --json PYMRM.json --group-depth 2 > PYMRM.out 2>&1
$CH scan --repo ~/Code/cce-code-health --ref cf15245 --json SELF.json --group-depth 2 > SELF.out 2>&1
$CH diff --repo ~/Code/cce-code-health --base cf15245~30 --head cf15245 --json selfdiff.json > selfdiff.out 2>&1
for f in *.json; do sed -i -E 's/"version": "[^"]*"/"version": "X"/g' $f; done
B=$(dirname $D)/before; ok=0; bad=0
for f in OFS.json OFS.out PYMRM.json PYMRM.out SELF.json SELF.out selfdiff.json selfdiff.out; do
  if cmp -s $B/$f $f; then ok=$((ok+1)); else bad=$((bad+1)); echo "DIFF $f"; fi; done
echo "G0: identical $ok, differ $bad"
