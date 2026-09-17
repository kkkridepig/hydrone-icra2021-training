"""Report execution, physical stress and paired outcomes separately."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import numpy as np
from diagnostic_core import METHODS
from core import json_write


def mean(values):
    return float(np.mean(values)) if values else None


def read_episodes(out):
    items, errors = [], []
    root = Path(out)/'episodes'
    for path in sorted(root.glob('*.json')):
        try:
            meta = json.loads(path.read_text())
            rows = []
            log = path.with_suffix('.jsonl')
            if log.exists():
                for number, line in enumerate(log.read_text().splitlines()):
                    try:
                        rows.append(json.loads(line))
                    except (ValueError, TypeError):
                        errors.append('%s: malformed line %d' % (log.name, number+1))
            if len(rows) != meta['steps']:
                errors.append(path.name+': metadata/log count mismatch')
            items.append((meta, rows))
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(path.name+': '+str(exc))
    for path in root.glob('*.jsonl'):
        if not path.with_suffix('.json').exists():
            errors.append(path.name+': missing terminal metadata')
    return items, errors


def summarize(items):
    groups = defaultdict(list)
    for meta, rows in items:
        groups[(meta['method'], meta['condition'], meta['direction'])].append((meta, rows))
    result = []
    for key, episodes in sorted(groups.items()):
        metas = [m for m, _ in episodes]
        interface = []
        for _, rows in episodes:
            errors = [abs(r['estimated_height']-r['pre']['position'][2]) for r in rows
                      if abs(r['pre']['position'][2]) < .3]
            if errors:
                interface.append(mean(errors))
        result.append(dict(method=key[0], condition=key[1], direction=key[2], episodes=len(metas),
            success_rate=mean([m['reason']=='success' for m in metas]),
            reasons=dict(Counter(m['reason'] for m in metas)),
            task_seconds_all=mean([m['simulation_seconds'] for m in metas]),
            peak_roll_pitch_deg=mean([m['peak_roll_pitch_deg'] for m in metas]),
            max_cross_track_m=mean([m['max_cross_track_m'] for m in metas]),
            interface_height_mae_macro_m=mean(interface),
            interface_mae_episode_count=len(interface),
            expert_queries_mean=mean([sum(r['expert_replanned'] for r in rows) for _, rows in episodes]),
            control_steps_mean=mean([len(rows) for _, rows in episodes]),
            decision_ms_p95_mean=mean([float(np.percentile([r['decision_ms'] for r in rows], 95))
                                       for _, rows in episodes if rows]),
            watchdog_ticks=sum(m['watchdog_ticks'] for m in metas),
            recovery_seconds_observed_only=mean([m['recovery_seconds'] for m in metas
                                                 if m.get('recovery_seconds') is not None]),
            recovery_censored_count=sum(m.get('recovery_censored', False) for m in metas),
            anchor_not_reached_count=sum(m['anchor'] is None for m in metas),
            replan_reasons=dict(Counter(r['replan_reason'] for _, rows in episodes for r in rows
                                       if r['replan_reason']))))
    return result


def paired(items):
    lookup = {(m['method'], m['seed'], m['direction'], m['condition']): m for m, _ in items}
    result = []
    for a, b in [('separated', 'geometry'), ('separated', 'unified'),
                 ('separated', 'long'), ('oracle', 'separated')]:
        for seed in sorted(set(m['seed'] for m, _ in items)):
            pairs = [(m, lookup[(b, s, d, q)]) for (method, s, d, q), m in lookup.items()
                     if method==a and s==seed and (b, s, d, q) in lookup]
            result.append(dict(comparison=a+' minus '+b, seed=seed, paired_episodes=len(pairs),
                success_difference=mean([float(x['reason']=='success')-float(y['reason']=='success') for x, y in pairs]),
                peak_roll_pitch_difference_deg=mean([x['peak_roll_pitch_deg']-y['peak_roll_pitch_deg'] for x, y in pairs]),
                max_cross_track_difference_m=mean([x['max_cross_track_m']-y['max_cross_track_m'] for x, y in pairs])))
    return result


def make_report(out, p, error=None):
    out = Path(out)
    items, errors = read_episodes(out)
    def read(name):
        path = out/name
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except ValueError:
            errors.append(name+': invalid JSON')
            return {}
    status, selection = read('worker_result.json'), read('selection.json')
    calibration = [(m, r) for m, r in items if m['split']=='calibration']
    tests = [(m, r) for m, r in items if m['split']=='test']
    directions = ['air_to_water', 'water_to_air']
    expected_cal = {(s, d, q['name'], method, 'clean' if q['name']=='control' else 'both')
                    for s in p['calibration_seeds'] for d in directions
                    for q in [dict(name='control')]+p['profiles'] for method in ['teacher', 'short', 'long']}
    actual_cal = {(m['seed'], m['direction'], m['profile'], m['method'], m['condition']) for m, _ in calibration}
    cal_complete = (len(calibration)==len(expected_cal) and actual_cal==expected_cal
                    and all(m['complete'] for m, _ in calibration))
    expected = {(s, d, q, method) for s in p['test_seeds'] for d in directions
                for q in p['conditions'] for method in METHODS}
    actual = {(m['seed'], m['direction'], m['condition'], m['method']) for m, _ in tests}
    complete = (cal_complete and status.get('status')=='completed' and error is None and not errors
                and len(tests)==len(expected) and actual==expected and all(m['complete'] for m, _ in tests)
                and selection.get('selected') in [q['name'] for q in p['profiles']]
                and all(m['profile']==selection.get('selected') for m, _ in tests))
    screened = (cal_complete and status.get('status')=='no_eligible_profile' and error is None
                and not errors and selection.get('selected') is None and not tests)
    diagnosis = ('NO_ELIGIBLE_VALIDATION_PROFILE' if screened else
                 'RESULTS_REQUIRE_REVIEW' if complete else 'INCOMPLETE')
    if complete and all(m['reason']=='success' for m, _ in tests):
        diagnosis = 'CEILING_REMAINS_REVIEW_CONTINUOUS_METRICS'
    result = dict(schema=1, diagnosis=diagnosis, execution_complete=bool(complete),
                  screening_complete=bool(screened), hypothesis_supported=None, error=error,
                  parse_errors=errors, worker=status, selection=selection,
                  calibration_complete=cal_complete, expected_calibration_episodes=len(expected_cal),
                  actual_calibration_episodes=len(calibration), expected_test_episodes=len(expected),
                  actual_test_episodes=len(tests), test_groups=summarize(tests),
                  calibration_groups_by_profile={q: summarize([(m, r) for m, r in calibration if m['profile']==q])
                                                 for q in ['control']+[q['name'] for q in p['profiles']]},
                  paired_seed_results=paired(tests))
    json_write(out/'report.json', result)
    lines = ['# 跨介质冻结策略诊断报告', '', '状态：`'+diagnosis+'`', '',
             '校准完成 %d/%d；测试完成 %d/%d。' % (len(calibration), len(expected_cal), len(tests), len(expected)),
             '模型未重新训练。执行完成不等于假设成立；本程序不自动判定创新有效。', '',
             '选定工况：`'+str(selection.get('selected'))+'`。筛选仅使用 validation seeds 与教师可行性/实际响应差。', '',
             '| 策略 | 工况 | 方向 | n | 成功率 | 峰值倾角° | 横向偏离m | 专家调用 |',
             '|---|---|---|---:|---:|---:|---:|---:|']
    for g in result['test_groups']:
        lines.append('| %s | %s | %s | %d | %.3f | %.3f | %.4f | %.1f |' %
                     (g['method'], g['condition'], g['direction'], g['episodes'], g['success_rate'],
                      g['peak_roll_pitch_deg'], g['max_cross_track_m'], g['expert_queries_mean']))
    lines += ['', '## 解释限制', '',
      '- 优先看按方向/扰动拆分的成功率、倾角、横向偏离，再看专家调用；三个环境种子不支持强统计结论。',
      '- 逐种子配对差值见 report.json；负倾角/偏离差表示前者较低。不可只挑有利工况。',
      '- oracle 只替换视觉注入和已完成动作区间的扰动标签，仍使用学习高度和同一专家；不是保证最优的上界。',
      '- 全部学生的专家输入仍含同一个原始运动风险预测；geometry 仅滤波/调度不使用事件风险。',
      '- unified 的专家条件输入已统一为原始运动预测，不能与旧 phase2 的 unified 数值直接归因比较。',
      '- 旧模型未用本次力脉冲/冻结帧训练；差距可能来自分布变化或专家覆盖不足，不能直接判定架构失败。',
      '- Gazebo 回执仅说明接受了施力请求；需看教师实际响应。插件 SetForce/SetTorque 的交互不等于已标定水动力。',
      '- 所有学生每周期运行事件编码器；专家调用减少不能直接宣称总算力/能耗降低。',
      '- recovery 是扰动结束后姿态与横向速度连续达标时间，不是位置恢复；必须同时看删失数。',
      '- teacher 使用仿真真值，学生仍依赖带噪声的仿真运动状态代理；本次不验证真实视觉惯性估计。',
      '- 若 NO_ELIGIBLE_VALIDATION_PROFILE，查看 selection.json 的基线、教师可行性与实际响应门槛；停止是实验筛选结果。',
      '- 若 INCOMPLETE，查看 ERROR.txt、worker.log、worker_result.json；不得作为算法失败样本。']
    if error:
        lines += ['', '运行错误：`'+error+'`']
    if errors:
        lines += ['', '证据错误：'+str(errors)]
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return result
