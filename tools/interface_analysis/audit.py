"""Read-only evidence audit. Usage: python3 audit.py /path/to/evidence.zip /path/to/output"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import zipfile
import numpy as np


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('evidence',type=Path)
    ap.add_argument('output',type=Path)
    a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(a.evidence) as z:
        names=set(z.namelist()); manifest=json.loads(z.read('evidence_manifest.json'))
        mismatches=[n for n,h in manifest.items() if n in names and hashlib.sha256(z.read(n)).hexdigest()!=h]
        missing=[n for n in manifest if n not in names]
        items=[]
        for n in sorted(names):
            if n.startswith('episodes/') and n.endswith('.json'):
                m=json.loads(z.read(n));rows=[json.loads(s) for s in z.read(n[:-5]+'.jsonl').decode().splitlines()]
                if len(rows)!=m['steps']:raise ValueError('Step count mismatch: '+n)
                items.append((m,rows))
        p=json.loads(z.read('protocol.json')); selection=json.loads(z.read('selection.json'))
    assert not mismatches,mismatches
    assert all(n.endswith('.npz') for n in missing),missing
    test=[(m,rs) for m,rs in items if m['split']=='test']
    calibration=[(m,rs) for m,rs in items if m['split']=='calibration']
    expected={(s,d,q,method) for s in p['test_seeds'] for d in ['air_to_water','water_to_air'] for q in p['conditions'] for method in p['methods']}
    actual={(m['seed'],m['direction'],m['condition'],m['method']) for m,rs in test}
    assert actual==expected and len(test)==len(expected)
    assert len(calibration)==36 and all(m['complete'] for m,rs in items)
    summary=[];heads={}
    for method in p['methods']:
        episodes=[(m,rs) for m,rs in test if m['method']==method]
        rows=[r for m,rs in episodes for r in rs]
        summary.append(dict(method=method,episodes=len(episodes),successes=sum(m['reason']=='success' for m,rs in episodes),
            crossed=sum(m['crossed'] for m,rs in episodes),expert_queries=sum(r['expert_replanned'] for r in rows),
            mean_peak_tilt_deg=float(np.mean([m['peak_roll_pitch_deg'] for m,rs in episodes])),
            mean_max_cross_track_cm=float(np.mean([m['max_cross_track_m'] for m,rs in episodes])*100),
            step_weighted_decision_ms=float(np.mean([r['decision_ms'] for r in rows]))))
        if method=='teacher':continue
        heads[method]={}
        for head,label in [('raw_visual','visual_label'),('raw_motion','motion_label_previous_interval')]:
            pos=[r[head] for r in rows if r[label]];neg=[r[head] for r in rows if not r[label]]
            clean=[r[head] for m,rs in episodes if m['condition']=='clean' for r in rs]
            macro=[float(np.mean([r[head]>=.5 for r in rs])) for m,rs in episodes if m['condition']=='clean']
            heads[method][head]=dict(positive_samples=len(pos),true_positives=sum(x>=.5 for x in pos),
                negative_samples=len(neg),false_positives=sum(x>=.5 for x in neg),
                recall=float(np.mean(np.array(pos)>=.5)),false_positive_rate=float(np.mean(np.array(neg)>=.5)),
                clean_samples=len(clean),clean_false_positives=sum(x>=.5 for x in clean),
                clean_false_positive_rate=float(np.mean(np.array(clean)>=.5)),
                clean_episode_macro_false_positive_rate=float(np.mean(macro)))
    failures=[]
    for m,rs in test:
        if m['reason']=='success':continue
        distances=[float(np.linalg.norm(np.array(r['post']['position'])-m['scenario']['goal'])) for r in rs]
        i=int(np.argmin(distances))
        failures.append(dict(seed=m['seed'],direction=m['direction'],condition=m['condition'],method=m['method'],
            reason=m['reason'],seconds_after_anchor=m['final']['time']-m['anchor'],min_goal_distance_m=distances[i],
            closest_xy_error_m=float(np.linalg.norm(np.array(rs[i]['post']['position'][:2])-m['scenario']['goal'][:2])),
            final_true_z=m['final']['position'][2],goal_z=m['scenario']['goal'][2],
            last_estimated_z=rs[-1]['estimated_height'],last_command_z=rs[-1]['physical_action'][1]))
    stats=dict(input_sha256=hashlib.sha256(a.evidence.read_bytes()).hexdigest(),
        integrity=dict(checked_files=len(manifest)-len(missing),hash_mismatches=mismatches,omitted_npz_files=len(missing)),
        calibration_episodes=len(calibration),test_episodes=len(test),
        watchdog_ticks=sum(m['watchdog_ticks'] for m,rs in items),
        dt_quantiles_s=np.percentile([r['dt'] for m,rs in items for r in rs],[0,50,95,100]).tolist(),
        selection=selection,summary=summary,event_head_metrics=heads,failures=failures,
        statistical_unit='scenario seed; steps and conditions are correlated, not independent replicates',
        hypothesis='No demonstrated stability advantage of separated scheduling in this batch')
    (a.output/'audit.json').write_text(json.dumps(stats,indent=2,ensure_ascii=False))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(12,8),constrained_layout=True)
    colors={'short':'#17803b','long':'#888888','geometry':'#bb7b13','separated':'#d84c3a','oracle':'#7656a5'}
    for method,color in colors.items():
        m,rs=next((m,rs) for m,rs in test if m['seed']==201 and m['direction']=='air_to_water' and m['condition']=='clean' and m['method']==method)
        t=np.array([r['post']['time']-m['initial']['time'] for r in rs]);zv=[r['post']['position'][2] for r in rs]
        dist=[np.linalg.norm(np.array(r['post']['position'])-m['scenario']['goal']) for r in rs]
        axes[0,0].plot(t,zv,label=method,color=color,lw=1.7)
        axes[0,1].plot(t,dist,label=method,color=color,lw=1.7)
        if method=='separated':
            tt=[r['pre']['time']-m['initial']['time'] for r in rs]
            axes[1,0].plot(tt,[r['pre']['position'][2] for r in rs],label='True height',color='#313d5a')
            axes[1,0].plot(tt,[r['estimated_height'] for r in rs],label='Estimated height',color=color)
            axes[1,0].plot(tt,[r['physical_action'][1] for r in rs],label='Vertical command (m/s)',color='#cc9200')
            axes[1,0].axhline(m['scenario']['goal'][2],color='k',ls='--',lw=1,label='Goal height')
    axes[0,0].axhline(-1.15,color='k',ls=':',lw=1,label='Failure boundary')
    axes[0,0].axhline(-.7072541988281831,color='k',ls='--',lw=1,label='Goal height')
    axes[0,1].axhline(.18,color='k',ls='--',lw=1,label='Success distance threshold')
    axes[0,0].set(title='Seed 201, clean air-to-water: height',xlabel='Elapsed simulation time (s)',ylabel='World height (m)')
    axes[0,1].set(title='Same case: distance to goal',xlabel='Elapsed simulation time (s)',ylabel='3D distance (m)',ylim=(0,.65))
    axes[1,0].set(title='Separated: estimate and command do not arrest descent',xlabel='Elapsed simulation time (s)',ylabel='Height (m) / command (m/s)')
    for ax in [axes[0,0],axes[0,1],axes[1,0]]:ax.grid(alpha=.2);ax.legend(fontsize=8)
    metrics=heads['separated'];x=np.arange(2);width=.35
    axes[1,1].bar(x-width/2,[metrics[k]['recall']*100 for k in ['raw_visual','raw_motion']],width,label='Injection-window recall',color='#2365a7')
    axes[1,1].bar(x+width/2,[metrics[k]['clean_false_positive_rate']*100 for k in ['raw_visual','raw_motion']],width,label='Clean-sample alarm rate',color='#d84c3a')
    axes[1,1].set(xticks=x,xticklabels=['Visual head','Motion head'],ylim=(0,112),ylabel='Percent',title='Separated event heads (threshold 0.5)')
    axes[1,1].legend(fontsize=8);axes[1,1].grid(axis='y',alpha=.2)
    fig.suptitle('Frozen-policy diagnostic audit | 36 calibration + 168 test episodes',fontsize=14)
    fig.savefig(a.output/'diagnostic-audit.png',dpi=160)
    plt.close(fig)
    print(json.dumps(dict(calibration=len(calibration),test=len(test),failures=len(failures),output=str(a.output))))

if __name__=='__main__':main()
