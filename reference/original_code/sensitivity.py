"""Recompute segmentation with fixed, independently varied detection cutoffs."""
import segment as seg
from segment import *
from itertools import product
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=seg.SOURCE);args=p.parse_args();seg.SOURCE=args.source
rows=[]
for s in ['S5','S6','S7']:
 cache={}
 for ch,f in product(['live','dead'],[.75,1,1.25]):cache[ch,f]=segment(read(s,ch),ch,f)
 for fg,fr in product([.75,1,1.25],repeat=2):
  lg,g,_=cache['live',fg];lr,r,_=cache['dead',fr];u,d=combine(g,r,lg,lr)
  rows.append(dict(sample=s,green_threshold_factor=fg,red_threshold_factor=fr,green_high=10*fg,red_high=4*fr,live_only=int((d.status=='live_only').sum()),dead_only=int((d.status=='dead_only').sum()),double_positive=int((d.status=='double_positive').sum()),total=len(d),viability_percent=100*(d.status=='live_only').mean()))
df=pd.DataFrame(rows);df.to_csv(OUT/'threshold_sensitivity.csv',index=False)
print(df.groupby(['green_threshold_factor','red_threshold_factor']).viability_percent.agg(['mean','std']).round(2).to_string())
