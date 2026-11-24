#!/usr/bin/env bash
# Poll a Kaggle kernel; print status + new [stage]/progress lines from its live log until it ends.
K=$1; seen=0
while true; do
  s=$(python3 tools/kaggle_cli.py kernels status "$K" 2>&1 | tail -1)
  lines=$(python3 tools/kaggle_cli.py kernels logs "$K" 2>/dev/null | python3 -c "
import sys,json
raw=sys.stdin.read(); i=raw.find('[')
try: d=json.loads(raw[i:])
except Exception: d=[]
out=''.join(r.get('data','') for r in d)
for l in out.splitlines():
    if l.startswith(('[stage]','[','ERROR','Traceback','SystemExit','smoke')) and 'NbConvert' not in l: print(l[:300])
")
  n=$(printf "%s\n" "$lines" | grep -c . )
  if [ "$n" -gt "$seen" ]; then printf "%s\n" "$lines" | tail -n +$((seen+1)); seen=$n; fi
  echo "$(date -u +%H:%M) $s"
  echo "$s" | grep -qiE "COMPLETE|ERROR|CANCEL" && break
  sleep 120
done
