"""Reports never equate successful execution with a supported hypothesis."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from core import json_write, load_config


def episodes(root):
    items = []
    for p in sorted((Path(root)/"episodes").glob("*.json")):
        if p.name.endswith(".preview.json"):
            continue
        m = json.loads(p.read_text())
        rows = [json.loads(x) for x in p.with_suffix(".jsonl").read_text().splitlines()] if p.with_suffix(".jsonl").exists() else []
        items.append((m, rows))
    return items


def stats(values):
    a = np.asarray(values, dtype=float)
    if len(a) == 0:
        return None
    return dict(mean=float(a.mean()), median=float(np.median(a)),
                p95=float(np.percentile(a, 95)), p99=float(np.percentile(a, 99)))


def collection_gate(root, c):
    items = episodes(root)
    expected = (len(c["train_seeds"])+len(c["validation_seeds"]))*2*4
    checks = dict(episode_count=len(items) == expected,
                  complete=bool(items) and all(m["complete"] for m, _ in items),
                  command_watchdog=bool(items) and all(m.get("watchdog_ticks", 0) == 0 for m, _ in items))
    summary = {}
    for direction in ("air_to_water", "water_to_air"):
        chosen = [m for m, _ in items if m["scenario"]["direction"] == direction and m["condition"] == "clean"]
        sr = sum(m["reason"] == "success" for m in chosen)/max(1, len(chosen))
        cr = sum(m["crossed"] for m in chosen)/max(1, len(chosen))
        checks[direction+"_success"] = sr >= c["gate_min_success_per_direction"]
        checks[direction+"_crossing"] = cr >= c["gate_min_crossing_per_direction"]
        summary[direction] = dict(clean_success_rate=sr, clean_crossing_rate=cr, n=len(chosen))
    rows = [r for _, rr in items for r in rr]
    if rows:
        image_std = float(np.median([r["image_std"] for r in rows]))
        image_age = float(np.median([r["pre"]["ages"]["image"] for r in rows]))
        dt_p95 = float(np.percentile([r["dt"] for r in rows], 95))
        checks["nonconstant_camera"] = image_std >= c["gate_min_image_std"]
        checks["camera_fresh"] = image_age <= c["gate_max_median_image_age"]
        checks["command_timing"] = dt_p95 <= c["gate_max_dt_p95"]
        for key in ("visual_active", "motion_label_previous_interval"):
            fraction = float(np.mean([r[key] for r in rows]))
            checks[key+"_coverage"] = c["gate_min_label_fraction"] <= fraction <= 0.99
        summary.update(image_std_median=image_std, image_age_median=image_age, dt_p95=dt_p95)
    else:
        checks["data_available"] = False
    result = dict(passed=all(checks.values()), checks=checks, summary=summary,
                  expected_episodes=expected, actual_episodes=len(items),
                  claim="Operational feasibility in the retained legacy model; NOT physical validation")
    json_write(Path(root)/"gate.json", result)
    return result


def group_summary(items):
    groups = defaultdict(list)
    for meta, rows in items:
        groups[(meta["method"], meta["condition"], meta["scenario"]["direction"])].append((meta, rows))
    output = []
    for key, values in sorted(groups.items()):
        rr = [r for _, rs in values for r in rs]
        mm = [m for m, _ in values]
        successful = [m for m in mm if m["reason"] == "success"]
        output.append(dict(method=key[0], condition=key[1], direction=key[2], n=len(mm),
            success_rate=len(successful)/len(mm),
            crossing_rate=sum(m["crossed"] for m in mm)/len(mm),
            terminal_reasons=dict(Counter(m["reason"] for m in mm)),
            successful_time=stats([m["simulation_seconds"] for m in successful]),
            peak_tilt=stats([max([max(abs(x) for x in r["post"]["rpy"][:2]) for r in rs], default=0) for _, rs in values]),
            peak_angular_rate=stats([max([np.linalg.norm(r["post"]["gyro"]) for r in rs], default=0) for _, rs in values]),
            decision_ms=stats([r["decision_ms"] for r in rr]),
            event_ms=stats([r["event_ms"] for r in rr]),
            expert_ms=stats([r["expert_ms"] for r in rr if r["expert_replanned"]]),
            dt=stats([r["dt"] for r in rr]),
            expert_calls=sum(r["expert_replanned"] for r in rr),
            executed_steps=len(rr),
            watchdog_ticks=sum(m.get("watchdog_ticks", 0) for m in mm),
            deadline_miss_fraction=sum(r["decision_ms"] > r.get("deadline_ms", 200) for r in rr)/max(1, len(rr))))
    return output


def paired(items):
    entries = {}
    for m, _ in items:
        if m["method"] == "teacher":
            continue
        s = m["scenario"]
        entries[(m["method"], m["model_seed"], s["seed"], s["direction"], m["condition"])] = m
    result = []
    for baseline in ("short", "long", "unified"):
        differences = defaultdict(list)
        for key, m in entries.items():
            if key[0] != "separated":
                continue
            other = entries.get((baseline,)+key[1:])
            if other:
                # Cluster by held-out scenario seed; directions and conditions
                # are not treated as independent training repetitions.
                differences[key[2]].append(int(m["reason"] == "success")-int(other["reason"] == "success"))
        values = np.asarray([np.mean(x) for x in differences.values()])
        if not len(values):
            continue
        rng = np.random.RandomState(908)
        bootstrap = np.asarray([rng.choice(values, len(values), replace=True).mean() for _ in range(2000)])
        result.append(dict(baseline=baseline, scenario_clusters=len(values),
                           success_difference=float(values.mean()),
                           exploratory_cluster_bootstrap_95=[float(x) for x in np.percentile(bootstrap, [2.5, 97.5])],
                           caveat="Pilot estimate; few clusters and model seeds do not establish publication-level significance"))
    return result


def make_report(root, c, phase, error=None):
    root = Path(root)
    items = episodes(root if phase == "phase1" else root/"evaluation")
    groups = group_summary(items)
    pairs = paired(items) if phase == "phase2" else []
    gate = collection_gate(root, c) if phase == "phase1" else None
    status_path = root/("worker_result.json" if phase == "phase1" else "evaluation/worker_result.json")
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    complete = status.get("status") == "completed" and error is None
    expected = ((len(c["train_seeds"])+len(c["validation_seeds"]))*8 if phase == "phase1" else
                len(c["test_seeds"])*8*(1+len(c["model_seeds"])*(len(c["methods"])-1)))
    complete = complete and len(items) == expected
    diagnosis = "INCOMPLETE" if not complete else (
        "READY_FOR_PHASE2" if phase == "phase1" and gate["passed"] else
        "BLOCKED_BY_FEASIBILITY_GATE" if phase == "phase1" else "PILOT_RESULTS_REQUIRE_REVIEW")
    test_teacher = [m for m, _ in items if m["method"] == "teacher"]
    teacher_success = sum(m["reason"] == "success" for m in test_teacher)/max(1, len(test_teacher))
    if phase == "phase2" and complete and teacher_success < c["gate_min_policy_teacher_success"]:
        diagnosis = "TEST_TEACHER_FEASIBILITY_LIMITATION"
    result = dict(phase=phase, execution_complete=complete, diagnosis=diagnosis, error=error,
                  groups=groups, paired= pairs, gate=gate, hypothesis_supported=None,
                  expected_episodes=expected, actual_episodes=len(items))
    if phase == "phase2":
        result["test_teacher_success_rate"] = teacher_success
        result["models"] = [
            json.loads(p.read_text()) for p in sorted((root/"models").glob("*/fit.json"))]
    json_write(root/"report.json", result)
    lines = ["# Hydrone 界面机制实验报告", "",
             "执行状态：**%s**。研究假设未被自动判定为成立。" % diagnosis, "",
             "本报告只汇总真实采集到的文件。静态检查、训练完成和假设得到支持是不同结论。", "",
             "实验保留原 Hydrone 水动力、浮力和电机模型；通过 UUV 服务改变阻尼系数。",
             "视觉扰动为受控图像处理，不是真实折射/飞溅。策略使用带噪声的仿真里程计代理；",
             "无绝对高度输入，图像模型估计高度。没有实现真实 VIO，也没有 DINOv3、FM 或 GRPO。",
             "相机编码与事件推理每个高层时刻都运行，本版只减少动作专家查询，不能据此声称视觉计算节省。", "",
             "目标为开阔场景中的双向跨越和目标到达；不是复杂避障或六篇论文的完整复现。", "",
             "期望/实际回合：%d / %d。" % (expected, len(items))]
    if error:
        lines += ["", "运行错误：" + str(error)]
    if gate:
        lines += ["", "阶段一门槛：", ""]
        lines += ["- %s: %s" % (k, "PASS" if v else "FAIL") for k, v in gate["checks"].items()]
    lines += ["", "| 方法 | 条件 | 方向 | 回合 | 成功率 | 跨界率 | 动作查询/执行步 |",
              "|---|---|---|---:|---:|---:|---:|"]
    for g in groups:
        lines += ["| %s | %s | %s | %d | %.3f | %.3f | %d/%d |" % (
            g["method"], g["condition"], g["direction"], g["n"],
            g["success_rate"], g["crossing_rate"], g["expert_calls"], g["executed_steps"])]
    lines += ["", "同场景配对比较（separated 减去基线）：", ""]
    for p in pairs:
        lines += ["- %s：成功率差 %.3f，探索性区间 %s，场景种子簇 %d。" % (
            p["baseline"], p["success_difference"], p["exploratory_cluster_bootstrap_95"], p["scenario_clusters"])]
    lines += ["", "下一步判断：", "",
              "- 若干净条件的教师无法双向跨越，先检查模型与控制权限，不继续学习模块。",
              "- 检查 models/*/fit.json 中的高度误差及两类事件识别；分类成功不等于控制获益。",
              "- 比较 separated 与 unified、short、long；若优势仅来自更频繁查询，不支持原因区分的贡献。",
              "- 检查原始日志中的复位、终止原因、时延、图像时效与实际阻尼读回。",
              "- 运动标签只表示上一已完成区间是否施加额外阻尼，不等同于真实水动力突变识别。",
              "- 事件输出未经独立概率校准，固定阈值用于试验；不提供失稳概率或安全保证。",
              "- 默认仅一个模型种子、三个测试场景种子，是初步实验，不是论文统计证明。",
              "- 所有参数与模型选择只使用训练/验证数据。测试结果不能回流后仍称独立测试。",
              "", "原始证据：episodes/*.jsonl、episodes/*.npz（阶段二位于 evaluation/），以及 provenance.json、config.json、运行日志。",
              "evidence.zip 自动包含报告、配置、日志、图像预览和清单；不包含大体积训练数组与模型权重，这些仍保存在服务器。"]
    (root/"REPORT.md").write_text("\n".join(lines)+"\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("output")
    p.add_argument("--phase", choices=["phase1", "phase2"], required=True)
    a = p.parse_args()
    make_report(a.output, load_config(Path(a.output)/"config.json"), a.phase)
