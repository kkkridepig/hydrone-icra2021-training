# 界面实验部署与证据保留

适用于已构建的 Ubuntu 20.04、ROS Noetic、Python 3.8、Gazebo Classic、Hydrone/UUV Simulator/RotorS/Lee 工作区。当前入口全部真实存在：

| 入口 | 输入 | 输出 |
|---|---|---|
| `tools/interface_experiment/run.bash phase1` | 既有环境与固定 config | 48 回合教师采集、完整 NPZ、门槛与 evidence |
| `tools/interface_experiment/run.bash phase2` | 完整成功 phase1 目录 | 训练、原 120 回合评估、模型与 evidence |
| `tools/interface_diagnostic/run.bash` | 成功 clockfix phase2 原目录与原权重 | 最多 36 校准＋168 测试；不训练 |
| `tools/interface_analysis/audit.py` | 诊断 evidence ZIP | JSON/图，仅读取输入，不启动仿真 |

本次仓库整理没有执行以下长时服务器命令，也没有实现提议中的 56 回合/到达后继续 10 秒功能。已有成功数据不需要为了整理 Git 而重跑。

## 环境与源码身份

常用根目录为 `/mnt/workspace/hydrone_ws`。PPU 的 torch 由镜像提供，**不得 pip 安装/替换 torch、torchvision、CUDA wheel，不升级 CUDA 或驱动**。历史 `phase1_evidence/accelerator.json` 记录 PPU-ZW810E、torch `2.0.0a0+nv2303`、Python3.8 site-packages；这是历史实测，不保证当前镜像相同。

已有服务器沿用原 `.venvs/hydrone-ppu` 和 `devel`。本次新增 Python/JSON/bash/xacro/launch 不要求重编译 catkin；重编译可能改变已被 provenance 记录的 40 项构建产物。新建服务器的基础安装见 [原服务器说明](../../tools/server/README.md)，但新构建不能冒充原成功构建身份。

```bash
export HYDRONE_ROOT=/mnt/workspace/hydrone_ws
export HYDRONE_BACKEND=ppu
cd "$HYDRONE_ROOT"
source tools/server/setup.bash
python -c 'import sys, torch; print(sys.version); print(torch.__version__); print(torch.__file__)'
```

实验 RGB 路径需要 ROS `gazebo_plugins` 相机插件及已配置的 Xvfb/Mesa（无 DISPLAY 时使用 `xvfb-run`/`xauth`）。这不同于原 DDPG/SAC LaserScan 入口。缺环境时保留实际错误，不用普通 CUDA torch 替换厂商运行时。

## 获取已整理源码，先保留现有服务器工作树

分支 `experiment/interface-mechanism-audit` 发布后，可拉取并在**新的、尚不存在的目录**只读审查：

```bash
cd /mnt/workspace/hydrone_ws
git status --short
git fetch origin experiment/interface-mechanism-audit
git worktree add --detach /mnt/workspace/hydrone_interface_review_20260917 \
  origin/experiment/interface-mechanism-audit
```

若审查目录已存在，改用新的名称。这个隔离 checkout 没有原服务器的 `devel`，用于审查/纯逻辑检查，不直接启动依赖原构建身份的冻结诊断。

原成功服务器很可能显示 `?? tools/interface_experiment/` 和 `?? tools/interface_diagnostic/`，这是历史 provenance 中的状态。不要直接覆盖或强制切换。先按下节比较内容；如果服务器源码与原包一致，运行链已经是本次整理的代码，可以继续使用原工作区、原环境和原成功数据。新增审计工具可从审查 checkout 单独运行。

如需让该旧工作区也跟踪新提交：先把未跟踪的实验目录及其他改动完整备份到仓库外，确认字节相同且备份可读；只迁移核实过的目录，保留真实差异供人工审查。当前有无关修改时继续使用隔离 worktree，不使用 `reset --hard`、`git clean -fd` 或强制覆盖。只有确认原工作区干净后，才可以执行下面的受保护切换：

```bash
(
  set -e
  cd /mnt/workspace/hydrone_ws
  test -z "$(git status --porcelain)" || {
    echo '工作树有改动：保留它们，先在隔离 worktree 审查。' >&2
    exit 1
  }
  git switch --detach origin/experiment/interface-mechanism-audit
)
```

不要重复应用初始包/clockfix/diagnostic 补丁。仓库已经按初始→clockfix→diagnostic 顺序集成，原补丁与完整文件两条交付路径只能选择一条。

## 不启动 Gazebo 的核验

在原服务器成功工作区，加载已有 PPU 环境后运行原测试：

```bash
cd /mnt/workspace/hydrone_ws
export HYDRONE_BACKEND=ppu
source tools/server/setup.bash
python -m unittest discover -s tools/interface_experiment/tests -v
python -m unittest discover -s tools/interface_diagnostic/tests -v
```

最终实验套件实际包含 33 项，诊断包含 17 项。网络测试在没有 torch 时会跳过，跳过不能写成通过。本次 Windows 离线结果为 31＋17 项通过、2 项网络测试跳过，服务器上的完整运行仍需验收。

用已有成功 phase2 目录只读核验源码/构建与原权重存在性：

```bash
cd /mnt/workspace/hydrone_ws
python - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path('tools/interface_diagnostic').resolve()))
from diagnostic_core import LEGACY
from core import load_config
import run as legacy_run
from diagnose import validate_source

source = Path('artifacts/interface_phase2_v2_clockfix').resolve()
config = load_config(source / 'config.json')
checkpoint = validate_source(source, config, legacy_run.provenance(config)['source_sha256'])
print('source/build/config verified; checkpoint SHA256:', legacy_run.sha(checkpoint))
expected = '83f8c9646a29d8ade96ac7a327b6923694eace9767fda0a769b0e16a4e5728ea'
assert legacy_run.sha(checkpoint) == expected, 'Not the model recorded by the supplied diagnostic evidence'
PY
```

这里的历史权重 hash 来自 diagnostic provenance；本次输入不含权重，尚未在本地读取权重验证。若成功目录命名不同，只调整 `source`。不能把 `evidence.zip` 当成该目录；必须存在 `config.json`、完成 120 回合的 `report.json`、`provenance.json` 与 `models/seed_0/model.pt`。

`Sources/build changed since successful phase2` 应据实际差异处理；不要删除检查、编辑历史 provenance 或冒用新构建哈希。代码里原有的复制后校验与运行前后权重哈希检查保持原样。

## 按需复现两阶段

以下为真正启动实验的手动命令，不是迁移必须执行的步骤。每次使用新的输出目录和外层日志。已完成阶段一时，可直接用原完整数据执行 phase2，无需重新采集。

```bash
cd /mnt/workspace/hydrone_ws
export HYDRONE_BACKEND=ppu
mkdir -p logs/interface
run_tag=$(date -u +%Y%m%dT%H%M%SZ)
phase1_output="$PWD/artifacts/interface_phase1_${run_tag}"
nohup bash tools/interface_experiment/run.bash phase1 \
  --output "$phase1_output" \
  > "logs/interface/phase1_${run_tag}.log" 2>&1 &
```

phase1 先进行 PPU 新网络算子、相机/控制链检查及干净双向可行性检查，再采集。必须查看 `REPORT.md`、`gate.json` 和实际回合，确认 `READY_FOR_PHASE2`。不能仅看进程退出。

```bash
cd /mnt/workspace/hydrone_ws
export HYDRONE_BACKEND=ppu
mkdir -p logs/interface
run_tag=$(date -u +%Y%m%dT%H%M%SZ)
nohup bash tools/interface_experiment/run.bash phase2 \
  --data "$PWD/artifacts/interface_phase1_v1" \
  --output "$PWD/artifacts/interface_phase2_clockfix_${run_tag}" \
  > "logs/interface/phase2_clockfix_${run_tag}.log" 2>&1 &
```

若使用新采集数据，替换 `--data` 为实际完整目录。原 `phase1_v1` 数据复用时应出现 `COLLECTION_COMPATIBILITY=clock_startup_hotfix_v1`；完全相同代码采集的数据可显示 `exact`。原迁移逻辑仅接受三项经审查的工程变化，保留原 phase1 provenance 和 240 项数据清单，在新 phase2 provenance 中记录迁移理由与前后哈希。

修复后日志顺序包含 `CLOCK_WAIT` 和 `CLOCK_READY: ... wallclock=false`。它们只证明时钟初始化状态，不证明任务成功。phase2 会重新训练并执行原 120 回合；不会复用失败 phase2 的模型，也不会覆盖原成功模型。

## 按需执行冻结诊断

只有原成功模型/配置/源码/构建身份核验通过后再启动。它不重新采集训练集或训练模型。

```bash
cd /mnt/workspace/hydrone_ws
export HYDRONE_BACKEND=ppu
mkdir -p logs/interface
run_tag=$(date -u +%Y%m%dT%H%M%SZ)
nohup bash tools/interface_diagnostic/run.bash \
  --from-phase2 "$PWD/artifacts/interface_phase2_v2_clockfix" \
  --output "$PWD/artifacts/interface_diagnostic_${run_tag}" \
  > "logs/interface/diagnostic_${run_tag}.log" 2>&1 &
```

预定顺序为 validation seeds 10/11 的 36 校准回合，然后在第一档合格扰动上执行新 seeds 200/201/202 的 168 测试回合。筛选不读取测试结果；若无合格档位就停止。最多 204 回合，单回合上限 65 仿真秒＋3 秒复位，墙钟上限 12 小时，不保证实际耗时。

两阶段入口默认 ROS/Gazebo 端口 11331/11361；diagnostic 默认 11431/11461。端口占用时拒绝启动，不结束其他任务。可传 `--port 11531`，同时使用 `port+30` 的 Gazebo 端口；不要并行启动资源冲突的批次。

## 日志、状态与保留

| 位置/状态 | 解释 |
|---|---|
| `logs/interface/*.log` | 环境加载、入口参数、进程启动等外层日志 |
| phase1 的 `worker.log`；phase2 的 `evaluation/worker.log`；diagnostic 的 `worker.log` | 逐回合进度；对应目录另有 gazebo.log/xacro.log/ROS 日志 |
| `READY_FOR_PHASE2` | 阶段一完整且采集门槛通过 |
| `BLOCKED_BY_FEASIBILITY_GATE` | 阶段一门槛未通过；不开始 phase2 |
| `PILOT_RESULTS_REQUIRE_REVIEW` | 初始对照完整，需研究判断；不自动证明假设 |
| `TEST_TEACHER_FEASIBILITY_LIMITATION` | phase2 教师可行性有限，限制方法比较解释 |
| `RESULTS_REQUIRE_REVIEW` | 诊断回合完整，需检查失败与连续指标 |
| `CEILING_REMAINS_REVIEW_CONTINUOUS_METRICS` | 仍有成功率天花板 |
| `NO_ELIGIBLE_VALIDATION_PROFILE` | 校准完整但无合格档位；筛选停止，退出码 3 |
| `INCOMPLETE` | 源码/模型/环境/执行/证据不完整；退出码 2，不能记为算法失败 |

`crossed` 只是曾进入另一介质区域；成功还需旧目标区域、速度、姿态及 1 秒保持。具体研究解释见 [状态文档](RESEARCH_STATUS_20260917.md)。

所有主入口要求输出目录不存在。失败时保留 `ERROR.txt`、`REPORT.md`、`report.json`、provenance 与 `evidence.zip`；若 shell 环境初始化先失败，可能只有外层日志。不要删除失败再复用目录名。

evidence 打包省略 `.pt`/`.npz` 与完整 ROS 日志树。原 phase1 数据、成功模型、运行配置、构建身份及完整日志应保留在持久化盘，并按 [交付物清单](ARTIFACT_INVENTORY.md) 在仓库外归档。模型/数组不进入 Git，也不以 evidence 替代它们。

## 离线重算

已有 numpy/matplotlib 的独立分析环境即可，不需要 ROS 或 torch。选择与证据目录不同、尚不存在的输出目录，因为原审计脚本允许覆盖自身输出文件。

```bash
test ! -e /path/to/new-audit-output && \
python tools/interface_analysis/audit.py \
  /path/to/diagnostic-evidence.zip /path/to/new-audit-output
```

若仅有解压目录，可按原相对路径重新 ZIP 后运行，须将它标注为新容器并记录新 SHA256，不能借用历史 ZIP hash。审计只验证 manifest 中存在的文件，允许省略 NPZ；本次对真实日志的重算不等于重新仿真或模型推理。原审计的三个浮点末位差异及原件缺口见 [验证记录](VALIDATION.md)。
