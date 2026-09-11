"""Tables and display-only figures. All microscopy panels read original scalar values."""
from pathlib import Path
from io import BytesIO
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from .io import read_scalar,json_write

METRICS=['viability_percent','ebfp_all_percent','ebfp_live_percent','double_positive_percent']


def fraction(n,d): return 100*n/d if d else np.nan


def count_summary(frame):
    n=len(frame);live=frame.status.eq('live_only');dead=frame.status.eq('dead_only');both=frame.status.eq('double_positive')
    # Legacy reference tables predate explicit channel availability.
    provided=frame.get('ebfp_channel_provided',pd.Series(True,index=frame.index)).fillna(False).astype(bool)
    scorable=frame.ebfp_scorable.fillna(False).astype(bool)&provided
    pos=frame.ebfp_detected.fillna(False).astype(bool)&scorable
    nl=int(live.sum());nd=int(dead.sum());nb=int(both.sum())
    return {'total':n,'live_only':nl,'dead_only':nd,'double_positive':nb,'viability_percent':fraction(nl,n),'ebfp_all_count':int(pos.sum()),'ebfp_all_scorable':int(scorable.sum()),'ebfp_all_unscorable':int((~scorable).sum()),'ebfp_all_percent':fraction(int(pos.sum()),int(scorable.sum())),'ebfp_live_count':int((pos&live).sum()),'ebfp_live_scorable':int((scorable&live).sum()),'ebfp_live_unscorable':int((~scorable&live).sum()),'ebfp_live_percent':fraction(int((pos&live).sum()),int((scorable&live).sum())),'double_positive_percent':fraction(nb,n),'viability_excluding_double_percent':fraction(nl,nl+nd),'green_any_percent':fraction(nl+nb,n),'ebfp_dead_count':int((pos&~live).sum()),'ebfp_dead_percent':fraction(int((pos&~live).sum()),int((scorable&~live).sum())),'ebfp_channel_not_provided':int((~provided).sum()),'ebfp_live_channel_not_provided':int((~provided&live).sum())}


def summarize(objects,entries,config,out):
    images=[];reps=[];area=np.prod(config['input']['expected_shape'])*np.prod(config['input']['pixel_size_um'])/1e6
    for e in entries:
        frame=objects[objects.image_id==e['image_id']]
        row={'image_id':e['image_id'],'replicate_id':e['replicate_id'],'ebfp_channel_provided':e.get('ebfp') is not None,**count_summary(frame)}
        row['detected_objects_per_mm2']=row['total']/area;images.append(row)
    for rep in dict.fromkeys(e['replicate_id'] for e in entries):
        frame=objects[objects.replicate_id==rep];fields=[e for e in entries if e['replicate_id']==rep]
        row={'replicate_id':rep,'n_fields':len(fields),'n_fields_with_ebfp':sum(e.get('ebfp') is not None for e in fields),**count_summary(frame)};reps.append(row)
    image_df=pd.DataFrame(images);rep_df=pd.DataFrame(reps)
    stats={m:{'mean':float(rep_df[m].mean()),'sample_sd':float(rep_df[m].std(ddof=1)),'n_replicates_with_defined_metric':int(rep_df[m].notna().sum())} for m in METRICS}
    image_df.to_csv(out/'image_summary.csv',index=False);rep_df.to_csv(out/'replicate_summary.csv',index=False);json_write(out/'aggregate_summary.json',stats)
    return image_df,rep_df,stats


def save_png(fig,path,dpi=220):
    buffer=BytesIO();fig.savefig(buffer,format='png',dpi=dpi,facecolor='white');path.write_bytes(buffer.getvalue())


def rgb(channels,config):
    """Make a display composite; mapped contains only channels actually acquired."""
    mapped={ch:np.clip((a-config['display'][ch][0])/(config['display'][ch][1]-config['display'][ch][0]),0,1) for ch,a in channels.items()}
    # An RG display needs a zero blue RGB component. This is not a measured image
    # and is never inserted into channels, mapped, segmentation, or EBFP scoring.
    display_blue=mapped['ebfp'] if 'ebfp' in mapped else np.zeros_like(mapped['green'])
    return np.stack([mapped['red'],mapped['green'],display_blue],axis=2),mapped


def _channels(entry,config):
    return {c:read_scalar(entry[c],config) for c in ['green','red','ebfp'] if entry.get(c) is not None}


def figures(entries,objects,image_df,rep_df,stats,config,out):
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none'})
    n_ebfp=sum(e.get('ebfp') is not None for e in entries);has_ebfp=n_ebfp>0
    valid=image_df[image_df.viability_percent.notna()]
    idx=(valid.viability_percent-valid.viability_percent.median()).abs().idxmin() if len(valid) else image_df.index[0]
    selected=image_df.loc[idx,'image_id'];entry=next(e for e in entries if e['image_id']==selected)
    channels=_channels(entry,config);combined,mapped=rgb(channels,config)
    h,w=channels['green'].shape;size_y=max(1,round(h*config['display']['central_crop_fraction']));size_x=max(1,round(w*config['display']['central_crop_fraction']));y0=(h-size_y)//2;x0=(w-size_x)//2;sl=(slice(y0,y0+size_y),slice(x0,x0+size_x))
    fig=plt.figure(figsize=(13 if has_ebfp else 10.5,8.5))
    fig.text(.05,.96,'Cell viability and detectable EBFP fluorescence' if has_ebfp else 'Cell viability from live/dead fluorescence',fontsize=19,weight='bold')
    subtitle=f'{len(entries)} fields; {len(rep_df)} replicate groups • mean ± sample SD • exploratory object counts'
    fig.text(.05,.92,subtitle,color='#59636e')
    if has_ebfp:
        fig.text(.05,.889,f'EBFP channel provided for {n_ebfp}/{len(entries)} fields; EBFP fractions use scorable objects from these fields only.',color='#59636e',fontsize=9)
    ncols=4 if has_ebfp else 3
    gs=fig.add_gridspec(2,ncols,left=.07,right=.96,bottom=.16,top=.84 if has_ebfp else .86,hspace=.65,wspace=.22,height_ratios=[1,1.05])
    plots=[(0,slice(0,2) if has_ebfp else slice(0,3),'A   Image-based viability',['viability_percent'])]
    if has_ebfp:plots.append((1,slice(2,4),'B   Detectable EBFP',['ebfp_all_percent','ebfp_live_percent']))
    for which,cols,title,metrics in plots:
        ax=fig.add_subplot(gs[0,cols]);ax.set_title(title,loc='left',weight='bold');ax.set_ylim(0,100);ax.set_ylabel('Objects (%)');ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
        for j,metric in enumerate(metrics):
            values=rep_df[metric].to_numpy();x=j+np.linspace(-.15,.15,len(values));color='#008d54' if which==0 else '#4466aa'
            ax.scatter(x,values,c=color,s=35,zorder=3)
            mean=stats[metric]['mean'];sd=stats[metric]['sample_sd']
            if np.isfinite(mean):ax.errorbar(j+.29,mean,yerr=sd if np.isfinite(sd) else None,fmt='_',color='#25313b',capsize=7,markersize=13)
            label=f'{mean:.1f} ± {sd:.1f}%' if np.isfinite(sd) else (f'{mean:.1f}% (SD undefined)' if np.isfinite(mean) else 'Undefined')
            ax.text(j,5,label,ha='center',color=color,weight='bold',fontsize=11)
        ax.set_xlim(-.45,len(metrics)-.4);ax.set_xticks(range(len(metrics)),['Live only / all detected'] if which==0 else ['All scorable objects','Scorable live-only objects'])
    zeros=np.zeros_like(mapped['green'])
    panels=[(np.stack([zeros,mapped['green'],zeros],2),'Live · green',False),(np.stack([mapped['red'],zeros,zeros],2),'Dead · red',False)]
    if has_ebfp:panels.append((mapped.get('ebfp'),'EBFP · grayscale',True))
    panels.append((combined,'Merged · RGB' if 'ebfp' in mapped else 'Merged · live/dead',False))
    for j,(panel,title,grayscale) in enumerate(panels):
        ax=fig.add_subplot(gs[1,j]);ax.set_title(title,fontsize=11);ax.axis('off')
        if panel is None:
            # Missing acquisition is explicitly annotated, never a fake dark image.
            ax.text(.5,.5,'Not acquired',ha='center',va='center',transform=ax.transAxes,color='#65717b',fontsize=12)
            ax.set_aspect('equal');continue
        ax.imshow(panel[sl],cmap='gray' if grayscale else None,vmin=0,vmax=1,interpolation='nearest')
        bar=config['display']['scale_bar_um'];length=bar/config['input']['pixel_size_um'][1]
        if length<size_x*.6:
            x=size_x*.92;y=size_y*.91;ax.plot([x-length,x],[y,y],c='white',lw=3);ax.text(x-length/2,y-size_y*.035,f'{bar:g} µm',ha='center',color='white',fontsize=8)
    fig.text(.05,.49,f'{"C" if has_ebfp else "B"}   Representative field {selected} · fixed central crop',weight='bold',fontsize=13)
    bounds='; '.join(f'{c} {config["display"][c][0]:g}–{config["display"][c][1]:g}' for c in channels)
    fig.text(.05,.10,'Display only: fixed linear scaling; '+bounds+'. Values above bounds display-clipped.',fontsize=8,color='#65717b');fig.text(.05,.075,'Source pixels unchanged. No gamma, denoising, or background correction in displayed microscopy panels.',fontsize=8,color='#65717b');fig.text(.05,.05,'Double-positive objects count as compromised. Review QC and sensitivity before biological interpretation.',fontsize=8,color='#65717b')
    save_png(fig,out/'figure.png');fig.savefig(out/'figure.svg');plt.close(fig)
    json_write(out/'figure_metadata.json',{'representative_image_id':selected,'crop_yx':[y0,y0+size_y,x0,x0+size_x],'display':config['display'],'n_fields_with_ebfp':n_ebfp,'representative_ebfp_channel_provided':'ebfp' in channels,'representative_source_channel_paths':{c:str(entry[c]) for c in channels}})
    qc=out/'qc';qc.mkdir()
    for e in entries:
        chans=_channels(e,config);composite,_=rgb(chans,config);d=objects[objects.image_id==e['image_id']]
        title=e['image_id']+' • green: live only; red: dead only; amber: both'
        title+='; cyan ring: EBFP' if 'ebfp' in chans else '; EBFP not acquired'
        fig,ax=plt.subplots(figsize=(12,12));ax.imshow(composite,interpolation='nearest');ax.axis('off');ax.set_title(title,fontsize=10)
        for row in d.itertuples():
            color={'live_only':'#00ff70','dead_only':'#ff6050','double_positive':'#ffd34d'}[row.status]
            ax.add_patch(Circle((row.x,row.y),4.5,fill=False,color=color,lw=.4));ax.text(row.x+5,row.y,str(row.object_id),color=color,fontsize=3)
            if 'ebfp' in chans and pd.notna(row.ebfp_detected) and row.ebfp_detected:ax.add_patch(Circle((row.x,row.y),6,fill=False,color='#30e8ff',lw=.4))
        fig.tight_layout();save_png(fig,qc/f'{e["image_id"]}_detections.png',dpi=160);plt.close(fig)
