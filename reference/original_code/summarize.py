from pathlib import Path
import pandas as pd,numpy as np,json
root=Path(__file__).resolve().parent.parent
obj=pd.read_csv(root/'data'/'ebfp_objects.csv');rows=[]
for s,d in obj.groupby('sample'):
 live=d.status=='live_only';double=d.status=='double_positive';dead=d.status=='dead_only';pos=d.ebfp_detected
 n=len(d);ng=int(live.sum());nd=int(dead.sum());nb=int(double.sum())
 rows.append(dict(sample=s,total=n,live_only=ng,dead_only=nd,double_positive=nb,viability_percent=100*ng/n,ebfp_all_count=int(pos.sum()),ebfp_all_percent=100*pos.mean(),ebfp_live_count=int(pos[live].sum()),ebfp_live_percent=100*pos[live].mean(),double_positive_percent=100*nb/n,ebfp_dead_count=int(pos[~live].sum()),ebfp_dead_percent=100*pos[~live].mean(),detected_objects_per_mm2=n/(2.32727**2),viability_excluding_double_percent=100*ng/(ng+nd),green_any_percent=100*(ng+nb)/n))
sumdf=pd.DataFrame(rows);sumdf.to_csv(root/'data'/'summary.csv',index=False)
metrics=['viability_percent','ebfp_all_percent','ebfp_live_percent','double_positive_percent','ebfp_dead_percent','viability_excluding_double_percent','green_any_percent']
stats={c:dict(mean=float(sumdf[c].mean()),sample_sd=float(sumdf[c].std(ddof=1))) for c in metrics}
(root/'data'/'aggregate_summary.json').write_text(json.dumps(stats,indent=2))
print(sumdf.round(3).to_string(index=False));print(json.dumps(stats,indent=2))
