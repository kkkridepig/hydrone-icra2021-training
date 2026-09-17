# Hydrone 下一步：冻结模型的界面事件诊断

本包只新增 `tools/interface_diagnostic/`。它复用服务器上已成功运行的 Hydrone＋Lee、时钟修复版 ROS 接口及 phase2 模型，不重新收集训练集、不训练、不安装包、不替换 vendor torch、不重新编译 catkin。

目标：在具有可测运动影响的扰动下，判断“分开处理视觉退化与运动扰动”是否比单一风险或仅靠界面距离的调度更有用。暂不引入 DINO、flow matching 或 GRPO。

## 1. 部署一次

将 `hydrone-interface-diagnostic-v1.zip` 上传到服务器 `/mnt/workspace/`，执行：

```bash
cd /mnt/workspace
unzip hydrone-interface-diagnostic-v1.zip -d hydrone-interface-diagnostic-v1
cd /mnt/workspace/hydrone_ws
git apply --check /mnt/workspace/hydrone-interface-diagnostic-v1/interface-diagnostic.patch
git apply /mnt/workspace/hydrone-interface-diagnostic-v1/interface-diagnostic.patch
mkdir -p logs/interface
```

补丁只新增目录，保留之前成功的代码。已经应用过时不要再次应用；若 `--check` 报冲突，保留输出，不要强制覆盖。ZIP 同时包含完整新增文件与 SHA256 清单供检查。

需要保留原始目录 `/mnt/workspace/hydrone_ws/artifacts/interface_phase2_v2_clockfix`，其中必须有：

- `config.json`、`report.json`、`provenance.json`；
- `models/seed_0/model.pt`。

必须使用完成 120 回合的原始 phase2 目录。上传到聊天的 `evidence.zip` 不包含模型权重，不能作为 `--from-phase2`。若你的成功目录名称不同，只修改下面这一参数。

## 2. 一条命令运行整批

```bash
cd /mnt/workspace/hydrone_ws
nohup bash tools/interface_diagnostic/run.bash \
  --from-phase2 /mnt/workspace/hydrone_ws/artifacts/interface_phase2_v2_clockfix \
  --output /mnt/workspace/hydrone_ws/artifacts/interface_diagnostic_v1 \
  > logs/interface/diagnostic_v1.log 2>&1 &
```

它自动加载已有服务器环境，默认独立 ROS/Gazebo 端口为 11431/11461。端口占用时会退出，不结束其他任务；可换用 `--port 11531`。建议避免同时跑其他仿真以减少实时性干扰。每次输出目录必须不存在；重跑用新的目录和日志名称，不删除失败证据。

```bash
tail -f /mnt/workspace/hydrone_ws/logs/interface/diagnostic_v1.log
```

开始仿真后，逐回合进度在：

```bash
tail -f /mnt/workspace/hydrone_ws/artifacts/interface_diagnostic_v1/worker.log
```

正常会先看到 `Frozen model and source identity verified`。代码逐项核对成功 phase2 的源文件/构建产物哈希以及配置，复制并校验权重。若出现 `Sources/build changed since successful phase2`，先回传报告核对变化；不要删掉检查。新增诊断目录不在旧程序的源文件清单中，不会因新增本包而触发不一致。

### 自动执行顺序

| 步骤 | 回合数 | 内容 |
|---|---:|---|
| 校准 | 36 | 原 validation seeds 10/11 × 双向 × clean 基线/两档 both 扰动 × teacher/short/long |
| 测试 | 168 | 新 seeds 200/201/202 × 双向 × 四类工况 × 七种策略 |

先完整运行两档校准；选择第一个同时满足以下条件的档位：clean 基线的三个策略全部成功且无 watchdog；受扰教师全部成功、实际进入视觉窗口且接受力请求、无 watchdog；相对匹配 clean 教师的平均峰值倾角增加至少 0.5°，或最大横向偏离增加至少 0.02 m。受扰学生的成败会报告，但不用于挑选档位；测试集和自适应策略的优势也不参与挑选。

这些是预先固定的诊断门槛，不是论文中的统计显著性门槛。校准不通过就停止；不要反复查看测试结果后调节门槛。

总计最多 204 回合，每回合最多 65 秒仿真加 3 秒复位稳定，约 3.9 小时仿真时间。墙钟时间取决于服务器实时因子及启动/服务开销，程序上限 12 小时。

## 3. 回传一个文件

结束后先看：

```bash
cat /mnt/workspace/hydrone_ws/artifacts/interface_diagnostic_v1/REPORT.md
```

然后上传这个文件：

```text
/mnt/workspace/hydrone_ws/artifacts/interface_diagnostic_v1/evidence.zip
```

| 状态 | 含义与下一步 |
|---|---|
| `RESULTS_REQUIRE_REVIEW` | 回合完整；检查按工况/方向的连续指标和配对结果，再判断机制是否值得继续。 |
| `CEILING_REMAINS_REVIEW_CONTINUOUS_METRICS` | 仍然全部成功；不能宣称成功率提升，进一步看稳定性差异是否一致。 |
| `NO_ELIGIBLE_VALIDATION_PROFILE` | 完成校准但没有合格工况；查看 `selection.json`，属于筛选停止，退出码 3。 |
| `INCOMPLETE` | 启动、接口、代码或证据不完整；看 `ERROR.txt`、`worker.log`，退出码 2。不能计作算法失败。 |

即使启动检查失败，Python 主入口也会尝试输出 REPORT.md 和 evidence.zip。若连 `setup.bash` 都失败、重复输出目录导致入口拒绝启动，回传外层 `logs/interface/diagnostic_v1.log`。不要因为没有 168 个测试回合就直接判定代码错误。

## 实验具体改变了什么

### 有限扰动，四格对照

在第一次满足真实高度 `abs(z)<0.30 m` 时记录触发点，只触发一次。真实高度仅供仿真注入、教师和评价使用，不输入学生策略。

| 档位 | 世界 Y 轴力 | 世界 X 轴力矩 | 施力时长 | 图像冻结＋污染时长 |
|---|---:|---:|---:|---:|
| mild | ±1.5 N | ±0.06 N·m | 0.8 s | 1.2 s |
| moderate | ±3.0 N | ±0.12 N·m | 1.2 s | 2.0 s |

符号由 seed 和跨越方向固定。同一场景不同策略使用相同参数。四种工况为 clean、仅视觉、仅动力扰动、两者都有。本轮不启用旧实验的额外阻尼扰动；原模型的 nominal damping 保持。

图像窗口冻结触发时的相机帧，再使用原污染函数，强度 0.24；窗口外恢复实时图像。这模拟短时视觉观测失效，不宣称真实水花/折射建模。

通过标准 Gazebo `apply_body_wrench` 服务对唯一解析的 base_link 施加有限世界坐标系力/力矩，每回合前后清除本体的待施力请求。服务回执不等于力传感器读数；日志记录请求与确认时间界，运动标签只采用已完成区间与可确认施力区间的交集，避免预知未来扰动。

参考接口与实现：

- [ApplyBodyWrench.srv](https://github.com/ros-simulation/gazebo_ros_pkgs/blob/noetic-devel/gazebo_msgs/srv/ApplyBodyWrench.srv)
- [Gazebo ROS API plugin](https://github.com/ros-simulation/gazebo_ros_pkgs/blob/noetic-devel/gazebo_ros/src/gazebo_ros_api_plugin.cpp)

该服务的 SetForce/SetTorque 与其他动力插件可能存在更新顺序影响，所以使用实际响应门槛。这是操作性压力测试，不是已校准的跨介质流体冲击模型。

### 七种策略，共用冻结专家

| 策略 | 高度滤波 | 动作 chunk 执行 |
|---|---|---|
| teacher | 真值 | 原规则教师，每周期反馈 |
| short | 固定增益 | 每步重算，执行一个动作 |
| long | 固定增益 | 每次执行完整 H=4 个动作 |
| geometry | 固定增益 | 仅接近界面且有竖直运动时缩短执行 |
| unified | 视觉/运动风险的 max | 接近界面或统一风险升高时重算 |
| separated | 视觉风险调滤波 | 接近界面或运动风险升高时重算 |
| oracle | 当前视觉注入标签调滤波 | 接近界面或上一已完成区间施力标签触发重算 |

geometry 的“仅几何”指滤波/调度规则，整个网络仍包含原专家的运动风险条件输入。为排除输入条件变化，所有学生的动作专家都接收同一个原始运动风险预测；oracle 不把注入真值替换进动作专家，也不使用真实高度。本轮 unified 的专家条件输入因此与旧 v1 不同，应在本轮内部比较。

“自适应”仅改变重新查询时机和执行长度，不学习 chunk 长度。H=4、控制周期 0.2 秒沿用旧配置。所有学生每周期仍计算事件模型，查询减少不能直接等同于总计算或能耗降低。

## 如何回答研究问题

1. 先看教师及固定策略是否受到可测扰动、是否仍有可行策略。清洁基线变差先检查冻结模型/接口覆盖。
2. 对照 separated 与 geometry，检查事件风险是否在“接近界面就缩短”之外提供稳定性收益。
3. 对照 separated 与 unified，并分别看视觉、运动、混合工况和双向跨越，避免混合均值掩盖负面效果。
4. 对照 oracle 与 separated：正确的注入时序是否提供改进空间？oracle 差也可能是规则或冻结专家不适配，不能简单解释为不存在研究价值。

主指标为成功率、峰值 roll/pitch、最大横向偏离。辅助指标包括每回合近界面高度 MAE 的宏平均、专家查询次数、决策耗时 P95、watchdog、扰动后姿态/横向速度恢复时间及其删失数。恢复不是位置恢复。逐 seed 配对差见 report.json；三个环境种子只用于诊断，不足以支撑论文级显著性结论。

旧事件头按阻尼条件训练，本轮改为有限力脉冲；视觉也新增了冻结。动作专家同样没有专门见过这些状态。因此本轮是冻结系统的压力诊断，可能暴露分布变化，不能把表现差直接归因为网络结构无效。若 oracle 也明显失败，应先检查专家覆盖与执行规则，再决定是否收集新数据。

这一轮仍不证明“动作已经失效”的因果判别：oracle 标签表示外加扰动发生，不是每个动作失效的真值。真实运动响应代理与扰动标签的差异应结合逐步日志解释。原仿真还保留固定电机系数、部分浮力不连续、未验证的水面视觉等限制，不能外推实机跨介质可靠性。

## 交付前已验证与未验证

已通过 17 项新增单元/接口契约检查与 18 项原契约检查，包括：执行索引、因果标签、共同专家输入、测试种子隔离、筛选规则、施力请求字段、源文件身份检查、报告完整性。测试中使用的假环境只验证程序契约，不属于研究结果。另验证了 Python 3.8 语法、bash 语法以及启动失败时的报告/打包路径。

尚未在你的 PPU/ROS/Gazebo 上运行本包。因此不能预先保证施力服务时序、扰动筛选或研究假设通过。此次服务器批次就是完成这一验证；不需要先升级任何学习框架。
