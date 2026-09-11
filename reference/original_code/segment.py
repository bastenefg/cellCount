from pathlib import Path
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist
import pandas as pd
import heapq,json,argparse
ROOT=Path(__file__).resolve().parent.parent
SOURCE=ROOT.parent.parent/'upload'
OUT=ROOT/'data'

def read(s,c):return np.array(Image.open(SOURCE/f'CHO-{s}-{c}.tif')).astype(float)

def segment(a,channel,factor=1, min_area=None):
 sigma=1.2 if channel=='live' else 1.0
 smooth=ndi.gaussian_filter(a,sigma)
 bg=ndi.gaussian_filter(a,12)
 contrast=smooth-bg
 high=(10.0 if channel=='live' else 4.0)*factor
 low=high/2
 min_area=min_area or (20 if channel=='live' else 12)
 mask=contrast>=low
 lab,n=ndi.label(mask,np.ones((3,3)))
 out=np.zeros(a.shape,np.int32); rows=[]
 for k,sl in enumerate(ndi.find_objects(lab),1):
  if sl is None:continue
  reg=lab[sl]==k
  if reg.sum()<min_area:continue
  z=contrast[sl];maxv=z[reg].max()
  if maxv<high:continue
  # Candidate peaks on smoothed contrast, with minimum 6px separation.
  pk=(z==ndi.maximum_filter(z,size=7,mode='constant'))&reg&(z>=high)
  yy,xx=np.where(pk);order=np.argsort(z[yy,xx])[::-1];seeds=[]
  for t in order:
   y,x=int(yy[t]),int(xx[t])
   if not seeds or min((y-y0)**2+(x-x0)**2 for y0,x0 in seeds)>=36:seeds.append((y,x))
  if not seeds:seeds=[np.unravel_index(np.argmax(np.where(reg,z,-np.inf)),z.shape)]
  # Priority flood watershed, seeds at intensity peaks.
  assigned=np.zeros(reg.shape,np.int32);heap=[]
  for j,(y,x) in enumerate(seeds,1):assigned[y,x]=j;heapq.heappush(heap,(-z[y,x],y,x,j))
  while heap:
   val,y,x,j=heapq.heappop(heap)
   for dy,dx in [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]:
    ny,nx=y+dy,x+dx
    if 0<=ny<reg.shape[0] and 0<=nx<reg.shape[1] and reg[ny,nx] and assigned[ny,nx]==0:
     assigned[ny,nx]=j;heapq.heappush(heap,(max(val,-z[ny,nx]),ny,nx,j))
  for j in range(1,len(seeds)+1):
   sub=assigned==j
   if sub.sum()<min_area:continue
   y,x=np.where(sub);gy,gx=y+sl[0].start,x+sl[1].start
   # Exclude frame-touching objects, same rule both channels.
   if (gy==0).any() or (gx==0).any() or (gy==1023).any() or (gx==1023).any():continue
   ident=len(rows)+1;out[gy,gx]=ident
   weights=z[y,x];cy=float(np.average(gy,weights=weights));cx=float(np.average(gx,weights=weights))
   rows.append(dict(channel=channel,id=ident,y=cy,x=cx,area=int(len(y)),peak_contrast=float(z[y,x].max()),mean_raw=float(a[gy,gx].mean()),max_raw=float(a[gy,gx].max()),parent_count=len(seeds)))
 return out,pd.DataFrame(rows),contrast

def combine(g,r,lg,lr):
 # Match segmented objects if centroids <=6px and masks overlap after 1px dilation.
 dist=cdist(g[['y','x']],r[['y','x']]); admiss=dist<=6
 glarge=ndi.maximum_filter(lg,size=3)
 overlap=set(zip(glarge[lr>0].tolist(),lr[lr>0].tolist()))
 for gi in range(len(g)):
  for ri in np.where(admiss[gi])[0]:
   cy,cx=g.iloc[gi][['y','x']]; yy,xx=int(round(cy)),int(round(cx));sl=(slice(max(0,yy-24),min(1024,yy+25)),slice(max(0,xx-24),min(1024,xx+25)))
   if not np.any(ndi.binary_dilation(lg[sl]==gi+1,structure=np.ones((3,3)))&(lr[sl]==ri+1)):admiss[gi,ri]=False
 cost=np.where(admiss,dist,1e6);gi,ri=linear_sum_assignment(cost);pairs={int(i):int(j) for i,j in zip(gi,ri) if admiss[i,j]}
 records=[];union=np.zeros(lg.shape,np.int32)
 for i,row in g.iterrows():
  matched=pairs.get(i);mask=lg==row.id
  if matched is not None:mask=mask|(lr==r.iloc[matched].id)
  cid=len(records)+1;union[mask]=cid
  records.append(dict(object_id=cid,y=row.y,x=row.x,green_id=int(row.id),red_id=int(r.iloc[matched].id) if matched is not None else 0,status='double_positive' if matched is not None else 'live_only',area=int(mask.sum()),green_peak=row.peak_contrast,red_peak=float(r.iloc[matched].peak_contrast) if matched is not None else 0))
 used=set(pairs.values())
 for i,row in r.iterrows():
  if i in used:continue
  mask=lr==row.id;cid=len(records)+1;union[mask]=cid
  records.append(dict(object_id=cid,y=row.y,x=row.x,green_id=0,red_id=int(row.id),status='dead_only',area=int(mask.sum()),green_peak=0,red_peak=row.peak_contrast))
 return union,pd.DataFrame(records)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=SOURCE);p.add_argument('--output',type=Path,default=OUT);args=p.parse_args();SOURCE=args.source;OUT=args.output;OUT.mkdir(parents=True,exist_ok=True)
 for s in ['S5','S6','S7']:
  lg,g,cg=segment(read(s,'live'),'live');lr,r,cr=segment(read(s,'dead'),'dead');union,df=combine(g,r,lg,lr)
  df.to_csv(OUT/f'{s}_objects.csv',index=False);g.to_csv(OUT/f'{s}_green.csv',index=False);r.to_csv(OUT/f'{s}_red.csv',index=False)
  np.savez_compressed(OUT/f'{s}_masks.npz',live=lg,dead=lr,objects=union)
  print(s,len(g),len(r),df.status.value_counts().to_dict(),'viability',100*(df.status=='live_only').mean())
