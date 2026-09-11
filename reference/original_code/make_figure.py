#!/usr/bin/env python3
"""Generate raw-intensity scientific figures from the locked analysis tables.

Reads the source TIFFs without editing them. Image panels apply only documented,
linear display mappings (identical across samples), pseudocolor, and a fixed crop.
No denoising, background subtraction, gamma, or image enhancement is performed.
Quantitative values are read verbatim from data/summary.csv.
"""
from pathlib import Path
from io import BytesIO
import json
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
UPLOAD = WORKSPACE / 'upload'
OUT = ROOT / 'figures'
OUT.mkdir(exist_ok=True)
PX_UM = 2327.27 / 1024
CROP = (320, 704, 320, 704)  # y start/stop, x start/stop; same for every channel
DISPLAY = {'live': (0., 120.), 'dead': (0., 30.), 'ebfp': (0., 34.)}
CHANNELS = ('live', 'dead', 'ebfp')
COLORS = {'live': '#008d54', 'dead': '#d54a3b', 'ebfp': '#4466aa'}
MARKERS = ['o', 's', '^']
SAMPLES = ['S5', 'S6', 'S7']
plt.rcParams.update({
    'font.family': 'DejaVu Sans', 'font.size': 10,
    'axes.titlesize': 12, 'axes.labelsize': 10.5,
    'axes.spines.top': False, 'axes.spines.right': False,
    'axes.linewidth': 0.8, 'xtick.major.size': 0, 'ytick.major.size': 3,
    'svg.fonttype': 'none', 'savefig.facecolor': 'white',
})


def write_png(fig, path, dpi=220):
    """Complete render to RAM before PIL writes the PNG to disk."""
    buffer = BytesIO()
    fig.savefig(buffer, format='png', dpi=dpi, facecolor='white')
    buffer.seek(0)
    with Image.open(buffer) as im:
        im.save(path, format='PNG')
    with Image.open(path) as im:
        im.verify()


def raw_channels(sample):
    return {ch: np.asarray(Image.open(UPLOAD / f'CHO-{sample}-{ch}.tif')).astype(float)
            for ch in CHANNELS}


def display_image(raw, kind):
    mapped = {ch: np.clip((raw[ch] - DISPLAY[ch][0]) /
                         (DISPLAY[ch][1] - DISPLAY[ch][0]), 0, 1)
              for ch in CHANNELS}
    if kind == 'ebfp':
        return np.repeat(mapped['ebfp'][..., None], 3, axis=2)
    out = np.zeros((*raw['live'].shape, 3), dtype=float)
    if kind in ('live', 'merge'):
        out[..., 1] = mapped['live']
    if kind in ('dead', 'merge'):
        out[..., 0] = mapped['dead']
    if kind == 'merge':
        out[..., 2] = mapped['ebfp']
    return out


def image_panel(ax, raw, kind, crop=None, scale_um=100, textsize=8.5):
    pic = display_image(raw, kind)
    if crop is not None:
        y0, y1, x0, x1 = crop
        pic = pic[y0:y1, x0:x1]
    ax.imshow(pic, interpolation='nearest', origin='upper')
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    height, width = pic.shape[:2]
    length = scale_um / PX_UM
    end_x = width * .92
    ypos = height * .91
    ax.plot([end_x-length, end_x], [ypos,ypos], c='white', lw=3,
            solid_capstyle='butt')
    ax.text(end_x-length/2, ypos-height*.025, f'{scale_um:g} µm',
            color='white', fontsize=textsize, ha='center', va='bottom')


def percent_axis(ax):
    ax.set_ylim(0, 100)
    ax.set_yticks(np.arange(0, 101, 20))
    ax.set_axisbelow(True)
    ax.grid(axis='y', color='#e5e8eb', linewidth=.7)
    ax.spines['left'].set_color('#8b949e')
    ax.spines['bottom'].set_color('#8b949e')
    ax.tick_params(colors='#39424c')


def metric(ax, vals, xpos, color, labels=None):
    offsets = [-.15, 0., .15]
    for i, (val, offset) in enumerate(zip(vals, offsets)):
        ax.scatter(xpos+offset, val, s=47, marker=MARKERS[i], color=color,
                   edgecolor='white', linewidth=.6, zorder=4)
    mean = float(np.mean(vals))
    sd = float(np.std(vals, ddof=1))
    ax.errorbar(xpos+.37, mean, yerr=sd, color='#202830', marker='_',
                markersize=14, capsize=5, elinewidth=1.5, capthick=1.2,
                zorder=5)
    return mean, sd


def label_panel(ax, letter, title):
    ax.text(-.15, 1.10, letter, transform=ax.transAxes,
            fontsize=15, fontweight='bold', va='bottom', ha='left')
    ax.set_title(title, loc='left', pad=17, fontweight='semibold')


def main():
    summary = pd.read_csv(ROOT/'data'/'summary.csv')
    summary['sample'] = summary['sample'].astype(str).str.replace('CHO-', '', regex=False)
    summary = summary.set_index('sample').loc[SAMPLES].reset_index()
    med = float(summary.viability_percent.median())
    representative = str(summary.iloc[np.argmin(abs(summary.viability_percent-med))]['sample'])
    fig = plt.figure(figsize=(12.8, 9.1), facecolor='white')
    fig.text(.043, .971, 'CHO–EBFP2 cells in PEG–PEG gels · 48 h',
             fontsize=19, fontweight='semibold', color='#1e2933', va='top')
    fig.text(.043, .933, 'Three imaged samples  •  Points = samples; black bars = mean ± sample SD',
             fontsize=10.5, color='#5b6672', va='top')
    legend = [Line2D([],[],color='#5b6672', marker=m, ls='', markersize=6, label=s)
              for m,s in zip(MARKERS, SAMPLES)]
    fig.legend(handles=legend, loc='upper right', bbox_to_anchor=(.969,.943),
               ncol=3, frameon=False, handletextpad=.5, columnspacing=1.25,
               fontsize=9.5)

    ax1 = fig.add_axes([.090, .555, .305, .297])
    percent_axis(ax1)
    label_panel(ax1,'A','Image-based viability')
    vm, vs = metric(ax1, summary.viability_percent.to_numpy(), 0, COLORS['live'])
    ax1.set_xlim(-.55,.80)
    ax1.set_xticks([.09], ['Live-only / all detected objects'])
    ax1.set_ylabel('Live-only objects (%)')
    ax1.text(.98, .055, f'{vm:.1f} ± {vs:.1f}%', ha='right', va='bottom',
             transform=ax1.transAxes, fontsize=13, color=COLORS['live'],
             fontweight='semibold')

    ax2 = fig.add_axes([.565, .555, .385, .297])
    percent_axis(ax2)
    label_panel(ax2,'B','Detectable EBFP fluorescence')
    em, es = metric(ax2, summary.ebfp_all_percent.to_numpy(), 0, COLORS['ebfp'])
    lm, ls = metric(ax2, summary.ebfp_live_percent.to_numpy(), 1.5, COLORS['ebfp'])
    ax2.set_xlim(-.48,2.23)
    ax2.set_xticks([.09,1.59], ['All detected objects','Live-only objects'])
    ax2.set_ylabel('EBFP-positive objects (%)')
    ax2.text(.24, .055, f'{em:.1f} ± {es:.1f}%', ha='center', va='bottom',
             transform=ax2.transAxes, fontsize=12, color=COLORS['ebfp'],
             fontweight='semibold')
    ax2.text(.80, .055, f'{lm:.1f} ± {ls:.1f}%', ha='center', va='bottom',
             transform=ax2.transAxes, fontsize=12, color=COLORS['ebfp'],
             fontweight='semibold')

    fig.text(.043, .461, 'C', fontsize=15, fontweight='bold', va='bottom')
    fig.text(.071, .462, f'Representative field · {representative}',
             fontsize=12, fontweight='semibold', va='bottom')
    fig.text(.95, .462, 'Fixed central crop · 873 × 873 µm',
             fontsize=9.5, color='#5b6672', ha='right', va='bottom')
    data = raw_channels(representative)
    headings = [('live','Live · green'), ('dead','Dead · red'),
                ('ebfp','EBFP · grayscale'), ('merge','Merged · RGB')]
    for i,(channel,title) in enumerate(headings):
        ax = fig.add_axes([.043+i*.234, .115, .217, .305])
        image_panel(ax, data, channel, CROP)
        ax.set_title(title, fontsize=10.5, loc='left', pad=7)

    fig.text(.043, .080,
             'Display: linear, fixed across samples. Green 0–120; red 0–30; EBFP 0–34 (8-bit units). Values above bounds are display-clipped.',
             fontsize=8.2, color='#5b6672')
    fig.text(.043, .059,
             'Original TIFFs unchanged. No gamma, denoising, or background correction in image panels. Scale bars, 100 µm.',
             fontsize=8.2, color='#5b6672')
    fig.text(.043, .032,
             'Exploratory object counts; n = 3 fields. Threshold sensitivity and biological replicate status are addressed in the methods.',
             fontsize=8.2, color='#5b6672')
    write_png(fig, OUT/'CHO_48h_main_figure.png', 240)
    fig.savefig(OUT/'CHO_48h_main_figure.svg', format='svg', facecolor='white')
    plt.close(fig)

    # Full fields make sample selection and spatial heterogeneity reviewable.
    fig,axes=plt.subplots(3,3,figsize=(12,12.8),facecolor='white')
    fig.subplots_adjust(left=.074,right=.975,bottom=.062,top=.919,wspace=.075,hspace=.16)
    fig.suptitle('CHO–EBFP2 at 48 h · all full fields',x=.074,y=.982,ha='left',
                 fontsize=18,fontweight='semibold')
    fig.text(.074,.948,'Identical linear display limits for all three samples; original TIFF intensity data.',
             fontsize=10.5,color='#5b6672')
    for row,sample in enumerate(SAMPLES):
        raw=raw_channels(sample)
        for col,ch in enumerate(CHANNELS):
            ax=axes[row,col]
            image_panel(ax,raw,ch,scale_um=250,textsize=9)
            ax.set_title(f'{sample}  |  '+ {'live':'Live · green','dead':'Dead · red','ebfp':'EBFP · grayscale'}[ch],
                         loc='left',fontsize=11,pad=7)
            # Indicate the same prespecified central area shown in the main figure.
            if sample == representative:
                from matplotlib.patches import Rectangle
                y0,y1,x0,x1=CROP
                ax.add_patch(Rectangle((x0,y0),x1-x0,y1-y0,fill=False,
                                       edgecolor='#f1e879',lw=.7,ls='--'))
    fig.text(.074,.035,
             'Display ranges: green 0–120, red 0–30, EBFP 0–34. Full fields: 2327.27 × 2327.27 µm. Scale bars: 250 µm.',
             fontsize=9,color='#5b6672')
    fig.text(.074,.018,
             f'Dashed boxes identify the fixed central crop used for representative sample {representative}.',
             fontsize=9,color='#5b6672')
    write_png(fig,OUT/'CHO_48h_all_fields.png',180)
    plt.close(fig)

    sensitivity_path=ROOT/'data'/'threshold_sensitivity.csv'
    if sensitivity_path.exists():
        sens=pd.read_csv(sensitivity_path)
        required={'sample','green_threshold_factor','red_threshold_factor','viability_percent'}
        if required.issubset(sens.columns):
            fig,axes=plt.subplots(1,3,figsize=(10.8,3.9))
            fig.subplots_adjust(left=.085,right=.97,bottom=.19,top=.80,wspace=.28)
            last=None
            for ax,sample in zip(axes,SAMPLES):
                sub=sens[sens['sample'].astype(str).str.replace('CHO-','',regex=False)==sample]
                pivot=sub.pivot_table(index='red_threshold_factor',columns='green_threshold_factor',
                                     values='viability_percent',aggfunc='mean').sort_index(ascending=False)
                last=ax.imshow(pivot.to_numpy(),vmin=0,vmax=100,cmap='YlGn',aspect='auto')
                ax.set_xticks(range(len(pivot.columns)),[f'{x:g}×' for x in pivot.columns])
                ax.set_yticks(range(len(pivot.index)),[f'{x:g}×' for x in pivot.index])
                ax.set_xlabel('Green threshold multiplier')
                ax.set_ylabel('Red threshold multiplier')
                ax.set_title(sample,fontweight='semibold')
                for r in range(len(pivot.index)):
                    for c in range(len(pivot.columns)):
                        val=pivot.iloc[r,c]
                        ax.text(c,r,f'{val:.1f}',ha='center',va='center',fontsize=8,
                                color='white' if val>63 else '#263238')
            fig.suptitle('Viability threshold sensitivity (%)',x=.085,ha='left',fontweight='semibold')
            write_png(fig,OUT/'CHO_48h_threshold_sensitivity.png',200)
            plt.close(fig)

    metadata={
        'representative_sample':representative,
        'representative_selection':'sample with median viability estimate',
        'crop_pixel_y_start_stop_x_start_stop':CROP,
        'pixel_size_um':PX_UM,
        'display_bounds_8bit':DISPLAY,
        'display_processing':'linear mapping and clipping only; EBFP shown grayscale separately and blue in RGB merge',
        'viability_mean_percent':vm,'viability_sample_sd_percent':vs,
        'ebfp_all_mean_percent':em,'ebfp_all_sample_sd_percent':es,
        'ebfp_live_mean_percent':lm,'ebfp_live_sample_sd_percent':ls,
    }
    (OUT/'figure_metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata,indent=2))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,default=UPLOAD);args=parser.parse_args();UPLOAD=args.source
    main()
