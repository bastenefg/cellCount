#!/usr/bin/env python3
"""Empirical EBFP-channel enrichment using translated, shape-matched cell masks.
Original image intensities are read only; no change to source TIFFs.
A cell is not a biological EBFP-positive control. q-values quantify enrichment
relative to cell-free image locations, not reporter specificity.
"""
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
from PIL import Image
from scipy import ndimage as ndi
from scipy.signal import correlate


def bh(p):
    p=np.asarray(p,float); order=np.argsort(p); out=np.empty_like(p)
    out[order]=np.minimum.accumulate((p[order]*len(p)/np.arange(1,len(p)+1))[::-1])[::-1].clip(0,1)
    return out


def measure(sample, source, masksdir, dilation=1, inner=18, outer=65, shift_y=0, shift_x=0):
    blue=np.array(Image.open(source/f'CHO-{sample}-ebfp.tif'),dtype=float)
    if shift_y or shift_x: blue=np.roll(blue,(shift_y,shift_x),axis=(0,1))
    labels=np.load(masksdir/f'{sample}_masks.npz')['objects']
    objects=pd.read_csv(masksdir/f'{sample}_objects.csv')
    occupied=ndi.binary_dilation(labels>0,iterations=2)
    result=[]
    for r in objects.to_dict('records'):
        object_id=int(r['object_id'])
        ys,xs=np.where(labels==object_id)
        if not len(ys): continue
        y0=max(0,int(ys.min())-dilation); y1=min(1024,int(ys.max())+dilation+1)
        x0=max(0,int(xs.min())-dilation); x1=min(1024,int(xs.max())+dilation+1)
        kernel=(labels[y0:y1,x0:x1]==object_id)
        if dilation: kernel=ndi.binary_dilation(kernel,iterations=dilation)
        # Do not borrow pixels already assigned to a neighboring object.
        kernel &= ((labels[y0:y1,x0:x1]==0)|(labels[y0:y1,x0:x1]==object_id))
        kernel=kernel.astype(float); area=kernel.sum()
        py0=max(0,y0-outer); py1=min(1024,y1+outer)
        px0=max(0,x0-outer); px1=min(1024,x1+outer)
        # 'valid' output indexes the top-left corner of each translated mask.
        sums=correlate(blue[py0:py1,px0:px1],kernel,mode='valid',method='fft')
        covered=correlate(occupied[py0:py1,px0:px1].astype(float),kernel,mode='valid',method='fft')
        dy=np.arange(sums.shape[0])+py0-y0
        dx=np.arange(sums.shape[1])+px0-x0
        rr=dy[:,None]**2+dx[None,:]**2
        eligible=(rr>=inner**2)&(rr<=outer**2)&(covered<.5)
        null=sums[eligible]
        obs=float((blue[y0:y1,x0:x1]*kernel).sum())
        p=float((1+np.count_nonzero(null>=obs-1e-5))/(len(null)+1))
        mean=float(obs/area)
        out={**r,'sample':sample,'ebfp_roi_pixels':int(area),'ebfp_sum':obs,'ebfp_mean':mean,
          'ebfp_nonzero_pixels':int(((blue[y0:y1,x0:x1]>0)*kernel).sum()),
          'ebfp_bg_mean':float(np.mean(null)/area) if len(null) else np.nan,
          'ebfp_bg_sd':float(np.std(null,ddof=1)/area) if len(null)>1 else np.nan,
          'ebfp_bg_q99':float(np.quantile(null,.99)/area) if len(null) else np.nan,
          'ebfp_shift_locations':len(null),'ebfp_p':p,
          'ebfp_enrichment':mean/(float(np.mean(null)/area)) if len(null) and np.mean(null)>0 else np.nan}
        result.append(out)
    return pd.DataFrame(result)


def main():
    p=argparse.ArgumentParser(); p.add_argument('--source',default='upload'); p.add_argument('--masks',default='analysis_tmp'); p.add_argument('--output',default='analysis_tmp'); p.add_argument('--dilation',type=int,default=1); p.add_argument('--inner',type=int,default=18); p.add_argument('--outer',type=int,default=65); p.add_argument('--shift-y',type=int,default=0); p.add_argument('--shift-x',type=int,default=0)
    a=p.parse_args(); out=Path(a.output); out.mkdir(parents=True,exist_ok=True)
    rows=pd.concat([measure(s,Path(a.source),Path(a.masks),a.dilation,a.inner,a.outer,a.shift_y,a.shift_x) for s in ['S5','S6','S7']],ignore_index=True)
    rows['ebfp_q_pooled']=bh(rows.ebfp_p.values)
    for name,ind in rows.groupby('sample').groups.items(): rows.loc[ind,'ebfp_q_sample']=bh(rows.loc[ind,'ebfp_p'].values)
    rows['ebfp_detected']=rows.ebfp_q_pooled<=.05
    rows.to_csv(out/'ebfp_objects.csv',index=False)
    summary={}
    for s,g in rows.groupby('sample'):
        z={'n':len(g),'null_locations_min':int(g.ebfp_shift_locations.min()),'null_locations_median':float(g.ebfp_shift_locations.median())}
        for q in [.01,.025,.05,.1]:
            det=g.ebfp_q_pooled<=q
            z[f'q{q}_n']=int(det.sum());z[f'q{q}_fraction']=float(det.mean())
            z[f'q{q}_live_fraction']=float(det[g.status=='live_only'].mean())
        z['by_status']={st:{'n':len(h),'detected':int(h.ebfp_detected.sum()),'fraction':float(h.ebfp_detected.mean())} for st,h in g.groupby('status')}
        for pth in [.001,.005,.01,.025,.05]:z[f'p{pth}_fraction']=float((g.ebfp_p<=pth).mean())
        summary[s]=z
    (out/'ebfp_summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
