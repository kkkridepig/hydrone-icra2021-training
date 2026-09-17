"""Frozen-policy diagnostic; no new trained policy or calibrated fluid model."""
import json
from pathlib import Path
import sys
import numpy as np
LEGACY = Path(__file__).resolve().parents[1]/'interface_experiment'
sys.path.insert(0,str(LEGACY))
from core import action_clip, corrupt_image, scenario

METHODS = ['teacher','short','long','geometry','unified','separated','oracle']
CONDITIONS = ['clean','visual','dynamics','both']

def load_protocol(path,c):
    p = json.loads(Path(path).read_text())
    if p['schema']!=1 or p['methods']!=METHODS or p['conditions']!=CONDITIONS:
        raise ValueError('Unexpected diagnostic schema/methods/conditions')
    if p['calibration_seeds']!=c['validation_seeds']:
        raise ValueError('Calibration must use the original validation seeds')
    old=set(c['train_seeds']+c['validation_seeds']+c['test_seeds']); test=p['test_seeds']
    if not test or len(set(test))!=len(test) or old.intersection(test) or min(test)<0:
        raise ValueError('New test seeds must be unique, nonnegative and previously unused')
    if not 0<p['trigger_height']<.5 or p['maximum_wall_hours']<=0:
        raise ValueError('Invalid trigger/wall-time bound')
    names=[]
    for q in p['profiles']:
        name=q['name']
        if not name.isidentifier() or name=='control' or name in names:
            raise ValueError('Invalid/duplicate profile name')
        names.append(name)
        if not (0<q['force_y_N']<=5 and 0<q['torque_x_Nm']<=.2 and .2<=q['force_seconds']<=2
                and .2<=q['vision_seconds']<=3 and 0<q['image_strength']<=.3):
            raise ValueError('Profile outside reviewed diagnostic bounds')
    if not names or len(names)>3:raise ValueError('One to three predeclared profiles required')
    for k in ('minimum_teacher_peak_increase_deg','minimum_teacher_cross_track_increase_m',
              'recovery_hold_seconds','recovery_roll_pitch_rad','recovery_lateral_speed'):
        if p[k]<=0:raise ValueError('Nonpositive '+k)
    return p

def make_spec(seed,direction,c,split):
    s=scenario(seed,direction,c,'validation' if split=='calibration' else 'test')
    # Only original starts/goals are used; original damping injection is disabled.
    s['diagnostic_split']=split
    s['pulse_sign']=-1.0 if (seed+(direction=='water_to_air'))%2 else 1.0
    return s

class EventWindow:
    def __init__(self,profile,condition,trigger_height):
        self.profile,self.condition,self.trigger_height=profile,condition,trigger_height
        self.anchor=self.frozen=self.receipt=None
    def observe(self,t,height,image,rng):
        if self.anchor is None and abs(height)<self.trigger_height:
            self.anchor=float(t);self.frozen=image.copy()
        visual=(self.anchor is not None and self.condition in ('visual','both')
                and t<self.anchor+self.profile['vision_seconds'])
        candidate=corrupt_image(self.frozen if visual else image,self.profile['image_strength'],rng)
        return candidate if visual else image,bool(visual)
    def needs_pulse(self):
        return self.anchor is not None and self.condition in ('dynamics','both') and self.receipt is None
    def completed_interval_label(self,start,end):
        if self.receipt is None:return False
        # Conservative overlap after acknowledgement, never a future label.
        return max(start,self.receipt['start_upper'])<min(end,self.receipt['end_lower'])
    def ended(self):
        ends=[]
        if self.anchor is not None and self.condition in ('visual','both'):
            ends.append(self.anchor+self.profile['vision_seconds'])
        if self.receipt:ends.append(self.receipt['end_upper'])
        return max(ends) if ends else None

def routing(method,raw_visual,raw_motion,current_visual_label,previous_motion_label):
    if method=='oracle':return float(current_visual_label),float(previous_motion_label),'separated'
    if method=='unified':
        q=max(raw_visual,raw_motion)
        return q,q,'unified'
    if method=='separated':return raw_visual,raw_motion,'separated'
    return 0.,0.,'short'

class Execution:
    def __init__(self,method,c):
        self.method,self.c=method,c
        self.chunk,self.index=None,0
    def reason(self,motion_risk,height,vz):
        if self.chunk is None or self.index==len(self.chunk):return 'empty_or_expired'
        if self.method=='short':return 'fixed_short'
        if self.method=='long':return None
        near=abs(height)<self.c['interface_guard_distance'] and abs(vz)>.025
        risk=self.method!='geometry' and motion_risk>=self.c['risk_threshold']
        return 'near_and_event' if near and risk else 'near_only' if near else 'event_only' if risk else None
    def replace(self,chunk):
        a=np.asarray(chunk)
        if a.shape!=(self.c['horizon'],3):raise ValueError('Wrong chunk shape')
        self.chunk=np.stack([action_clip(row) for row in a]);self.index=0
    def next(self):
        if self.chunk is None or self.index>=len(self.chunk):raise RuntimeError('Action chunk expired')
        action=self.chunk[self.index].copy();self.index+=1
        return action

def cross_track(position,spec):
    direction=np.asarray(spec['goal'][:2])-spec['start'][:2]
    delta=np.asarray(position[:2])-spec['start'][:2]
    norm=np.linalg.norm(direction)
    return float(np.linalg.norm(delta) if norm<1e-6 else abs(direction[0]*delta[1]-direction[1]*delta[0])/norm)

def choose_profile(metas,p):
    """Validation only, without selecting on an adaptive policy's advantage."""
    if any(m['split']!='calibration' or m['seed'] not in p['calibration_seeds'] for m in metas):
        raise ValueError('Profile selection cannot read test episodes')
    lookup={(m['profile'],m['seed'],m['direction'],m['method']):m for m in metas}
    keys=[(s,d) for s in p['calibration_seeds'] for d in ('air_to_water','water_to_air')]
    controls=[lookup[('control',s,d,m)] for s,d in keys for m in ('teacher','short','long')]
    baseline=all(m['reason']=='success' and m['watchdog_ticks']==0 for m in controls)
    checks=[];selected=None
    for profile in p['profiles']:
        teachers=[lookup[(profile['name'],s,d,'teacher')] for s,d in keys]
        da=np.mean([m['peak_roll_pitch_deg']-lookup[('control',m['seed'],m['direction'],'teacher')]['peak_roll_pitch_deg'] for m in teachers])
        dx=np.mean([m['max_cross_track_m']-lookup[('control',m['seed'],m['direction'],'teacher')]['max_cross_track_m'] for m in teachers])
        feasible=all(m['reason']=='success' and m['pulse_receipt'] is not None and m['visual_frames']>0
                     and m['watchdog_ticks']==0 for m in teachers)
        informative=da>=p['minimum_teacher_peak_increase_deg'] or dx>=p['minimum_teacher_cross_track_increase_m']
        passed=baseline and feasible and informative
        checks.append(dict(profile=profile['name'],feasible=feasible,informative=bool(informative),
                           teacher_peak_delta_deg=float(da),teacher_cross_track_delta_m=float(dx),passed=bool(passed)))
        if passed and selected is None:selected=profile['name']
    return dict(selected=selected,baseline_success=baseline,profiles=checks,
                rule='First teacher-feasible, physically informative validation profile; no test access')
