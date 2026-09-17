# 交付物、源码与提交映射

盘点日期：2026-09-17。仓库：`kkkridepig/hydrone-icra2021-training`。

## 接收形式与仓库基线

实际收到的是下列已解压目录，**没有任何原始 ZIP 文件**。因此所有原 ZIP 的 SHA256 均为未知，未执行或声称原 ZIP 解压/路径穿越检查。目录内未发现链接或越界路径；补丁中的目标均在对应实验目录内。临时重打包 evidence 另行校验了路径、重复成员与 CRC，但它不替代原 ZIP。

初始目录是附件区，不是 Git 工作树。新克隆隔离 checkout 后，`git status` 干净；仓库内未找到 `AGENTS.md`，按任务提供的全局说明执行。`origin/main` 与 HEAD 均为 `8790a9ea1598c779e38e1fa5eeb3eeec6ca87bcf`，与旧实验基线无差异，没有回滚 main。工作分支为 `experiment/interface-mechanism-audit`。

[input-inventory.json](2026-09-17/input-inventory.json) 对 8 个输入目录的 **1382 个文件**逐一记录相对路径、字节数和 SHA256。下表的“目录清单哈希”是对按路径排序的 `文件SHA256 + 两个空格 + 相对路径 + LF` 拼接文本计算 SHA256，**不是 ZIP 哈希**。

| 实际目录 | 文件数 | 字节数 | 身份与职责 |
|---|---:|---:|---|
| `hydrone-interface-pilot-v1` | 17 | 237799 | 初始两阶段实验；14 源文件及补丁哈希全部匹配 manifest |
| `hydrone-phase2-clockfix-v1` | 7 | 64705 | 唯一工程修复包；3 源文件及补丁哈希匹配 |
| `hydrone-interface-diagnostic-v1` | 12 | 139863 | 仅新增冻结模型诊断；9 文件及补丁哈希匹配 |
| `hydrone-diagnostic-audit-20260917` | 4 | 295638 | 离线审计脚本、历史报告/JSON/图；没有原 package manifest |
| `phase1_evidence` | 206 | 12258271 | 48 回合教师采集证据；不是训练集完整副本 |
| `phase2_evidence` | 15 | 177055 | 首次训练后评估启动失败，0 回合；必须保留 |
| `第三次_evidence` | 495 | 37667386 | 内容确认是 clockfix 后成功 phase2，120 回合；不由中文目录名推断版本 |
| `hydrone-interface-diagnostic-v1_evidence` | 626 | 52805138 | 36 校准＋168 测试，冻结诊断原始日志与元数据 |

| 实际目录 | 目录清单 SHA256 |
|---|---|
| `hydrone-interface-pilot-v1` | `a37ecb31d17ce50dbb8ee056f01320d9645cc873df78acb10600a723158ae43f` |
| `hydrone-phase2-clockfix-v1` | `16ff9cf512aef2bca96e719dc4807b13473e7a8639218370d8f63647390bf408` |
| `hydrone-interface-diagnostic-v1` | `df28e9f6af16424ae1c35037cc12ee74cece2f929612093350751076a50493aa` |
| `hydrone-diagnostic-audit-20260917` | `924c6e7c107a5504a9e5ec2cd33146df06258e583bd3d974e24fc32fdbee607c` |
| `phase1_evidence` | `4edffb25ad7c6477369bf70468aa246ddd64b60fd08c5bff241bf99799daec3b` |
| `phase2_evidence` | `71e9522fdbcbd6771da47c468b8d81276d2147a88d90b8b19ef1afcc8b2d8d66` |
| `第三次_evidence` | `67ca5733ed6b5fb6fa07a256a58d5957f7547d72b33c59ebed526c4a093c25d5` |
| `hydrone-interface-diagnostic-v1_evidence` | `8ad396b0d3026551f522dd2aca190102e188ae3cb25ce6321b45834fc3cfad40` |

另有研究总结 `HYDRONE_RESEARCH_AND_CODE_REVIEW_20260917.md`，SHA256 为 `a5177525c42e06b5ef00f6f8f2f9c081ea82be98b05b7de3c4acd8b9f4270df5`。它提供解释线索；源码与实验数值仍以实际文件为准，没有把总结中的旧权限说明或缺包说明当成当前状态。

## 真实应用顺序与提交

采用且只采用**补丁路径**集成三个源码包。每个补丁均先 `git apply --check` 再 `git apply`，随后逐文件比较 SHA256 与包内完整文件/manifest。完整源码副本仅用于交叉核验，没有再次覆盖。审计包没有补丁，`audit.py` 单次按字节复制到独立目录。

| 顺序 | manifest 声明的来源 | 本次提交 | 内容 |
|---|---|---|---|
| 1 | 基线 `8790a9e`；包声明本地提交 `5cbc8e241561cd0382e3e60c66bac512e5717b1a` | `a1dbe2c8f0fecc398c11335e5cd3034e7a315848` | 初始 `tools/interface_experiment/` |
| 2 | 前置本地提交 `5cbc8e2`；修复声明 `2a3c6bacc626d33ea18ccecbf10991d5d11f306f` | `8faf55fe4ba0b0201d5228b40bee3c724903aa43` | 修改 ros_io.py/run.py，新增 test_startup.py |
| 3 | 基线 `8790a9e`，但运行明确依赖成功 clockfix 源码/权重 | `22074bd8398e347d8bbc4cb0191ddba87020ce6e` | 仅新增 `tools/interface_diagnostic/` |
| 4 | 原审计无 Git 来源声明 | `bee961dd7edb5eec94ffc5b350c4a056e5e9cc18` | `tools/interface_analysis/audit.py`、历史小型报告、重算结果、输入与包 manifest、字节保留属性 |

本地来源提交号是原 manifest 的声明，不声称它们已存在于远端 Git 对象库。本次实际提交号与原作者提交号不同。新文档与 README 的提交可用 `git log -- docs/experiments/ARTIFACT_INVENTORY.md README.md` 追溯。

| 补丁 | SHA256 | 可核对的前置关系 |
|---|---|---|
| `hydrone-interface-pilot-v1.patch` | `8075be5c3653b9458383c828b976ba5bef04445a71851a621d28a86d7d705d78` | 基线上新增实验目录 |
| `phase2-clockfix.patch` | `43d8211243c9d733bf01aee4945a91c6ff65732a0c54c5224b9329b14e971095` | `ros_io.py` blob `8353a86→0c818d5`，`run.py` blob `f9b0848→0642660`；完整 SHA256 见下表 |
| `interface-diagnostic.patch` | `9df4827c284551239f1ae52f3135211032ba91e639c159334a66a75508b6d0fd` | 只新增诊断目录，不覆盖已有实验 |

没有找到第二个 hotfix，不构造不存在的应用步骤。原包 manifest 原样保存在 [package-manifests](2026-09-17/package-manifests/)。

## 每个交付文件的职责与去向

初始包的下列相对路径均位于 `tools/interface_experiment/`，在提交 `a1dbe2c` 入库。除明确列出的 clockfix 覆盖项外，最终字节保持初始包版本。

| 文件 | 职责 | 最终版本 |
|---|---|---|
| `config.json` | 种子、周期、H、采样/训练/终止阈值 | 初始包原样 |
| `core.py` | 观测/动作契约、真值教师、扰动、历史、滤波、执行及终止 | 初始包原样 |
| `learning.py` | CNN/历史 MLP/动作 MLP、masked L1、训练与模型加载 | 初始包原样 |
| `worker.py` | 两阶段真实采集/评估循环与逐步证据 | 初始包原样 |
| `ros_io.py` | ROS/Gazebo I/O、原控制桥、时钟与反馈健康检查 | 被真实 clockfix 覆盖 |
| `run.py` | 入口、provenance、阶段门槛、进程、报告打包 | 被真实 clockfix 覆盖 |
| `run.bash` | 加载既有 PPU/ROS 环境 | 初始包原样 |
| `report.py` | 采集门槛、五方法统计与完成状态 | 初始包原样 |
| `assets/robot.xacro` | 独立实验 RGB 相机配置 | 初始包原样 |
| `assets/simulation.launch` | 启动既有 Hydrone/Lee/UUV/RotorS | 初始包原样 |
| `tests/test_contracts.py` | 18 项因果/观测/动作/报告契约 | 初始包原样 |
| `tests/test_learning.py` | 2 项实际 CPU 网络更新与 checkpoint 检查 | 初始包原样，本次因无 torch 跳过 |
| `README.md` | 原实验部署说明 | 历史原件，当前部署见新文档 |
| `LOCAL_VALIDATION.md` | 交付时本地验证记录 | 历史原件，不当成本次执行结果 |

初始包顶层 `DEPLOY.md` 是包交付说明，不覆盖根 README；其 hash 纳入清单、原件外部保留。`PACKAGE_MANIFEST.json` 原样归档；`.patch` 只用于应用与审查，不重复入库为运行入口。

clockfix 包的三个源码文件在原路径由 `8faf55f` 集成。`tests/test_startup.py` 是 13 项时钟竞态、传感器启动与受限迁移回归。顶层 `README.md`/`DIAGNOSIS.md` 解释故障与部署，保留其原件身份，内容核实后纳入当前文档；不冒充 Python 入口。包 manifest 原样归档，patch 只应用一次。

diagnostic 包的下列文件均在 `tools/interface_diagnostic/` 由 `22074bd` 新增，字节与原包一致。

| 文件 | 职责 |
|---|---|
| `protocol.json` | 校准/测试种子、施力档位与预定选择门槛 |
| `diagnostic_core.py` | 单次窗口、因果标签、共同专家条件、调度与筛选 |
| `diagnostic_ros.py` | 唯一 base_link、Gazebo 施力/清理服务、请求时序边界 |
| `diagnostic_worker.py` | 冻结模型的 36＋168 回合及逐步证据 |
| `diagnose.py` | 成功 phase2 身份验证、模型复制/运行前后 hash、独立端口与失败打包 |
| `diagnostic_report.py` | 组合完整性、连续指标、配对统计与停止状态 |
| `run.bash` | 加载既有服务器环境 |
| `tests/test_diagnostic.py` | 17 项因果窗口、共同输入、筛选与来源契约 |
| `README.md` | 原交付说明，保留字节 |

diagnostic 顶层 `DEPLOY.md` 是部署文档副本，外部保留；manifest 原样归档，patch 仅应用一次。

| audit 包文件 | 入库位置 | 处理 |
|---|---|---|
| `audit.py` | `tools/interface_analysis/audit.py` | 字节不变；只读输入、写独立审计输出 |
| `audit.json` | `docs/experiments/2026-09-17/audit-original/audit.json` | 历史结果原样保留 |
| `REPORT.md` | `docs/experiments/2026-09-17/audit-original/REPORT.md` | 历史中文解释与未执行的 56 回合提案 |
| `diagnostic-audit.png` | `docs/experiments/2026-09-17/audit-original/diagnostic-audit.png` | 原图原样保留 |

报告同名文件没有覆盖任何运行入口。新 `audit-recomputed.json` 是本次产生的独立结果，不覆盖原审计。新生成 JSON 以 UTF-8/LF 保存；原源码、原报告和 manifest 未格式化。`.gitattributes` 对导入原件关闭文本转换，避免未来 checkout 改变行尾。

## 证据文件职责与外部归档

每个 evidence 文件的相对路径和 hash 均在 input-inventory.json；下表按文件类型解释职责，长日志、相机预览、URDF 与重复 ZIP 不进入 Git。

| 路径/类型 | 职责与保留规则 |
|---|---|
| `config.json`、`protocol.json` | 运行配置与诊断协议；保留原字节 |
| `provenance.json` | 历史源码/构建/配置/模型身份与迁移记录；不可重写以绕过检查 |
| `dataset_manifest.json`、`evidence_manifest.json` | 原回合文件哈希；缺省省略 NPZ 不等于训练数据齐全 |
| `episodes/*.json`、`evaluation/episodes/*.json` | 回合元数据、组合、终止原因、steps |
| `*.jsonl` | 真值/预测/动作/因果标签/耗时的逐步日志，或训练曲线 |
| `*.preview.json`、`*.png` | 相机预览和对应标签；不是全量相机数组 |
| `REPORT.md`、`report.json`、`gate.json`、`selection.json` | 历史报告、阶段门槛和筛选结果 |
| `worker_result.json` | 程序执行状态；不能单独证明组合完整 |
| `models/seed_0/fit.json` 等小型训练记录 | 已完成训练的指标；不是 `model.pt` |
| `accelerator.json` | 该次历史 PPU/torch 算子检查记录；不是当前服务器探测 |
| `sensor_preflight.json`、`physics_inventory.json`、`expanded_robot.urdf` | 传感控制链与实际展开模型检查 |
| `ERROR.txt`、`gazebo.log`、`worker.log`、`xacro.log` | 故障/启动/退出证据；失败运行也保留 |

建议把四组 evidence、四类交付原目录、研究总结及未来补齐的原 ZIP，连同本清单，归档在受控持久化存储的独立 `interface-20260917` 目录，并登记实际存储 URI 与访问责任人；本次未创建外部上传，也不虚构存储地址。源文件清单允许之后复核归档内容，不能恢复已缺失的数据字节。

服务器必须另存完整 `artifacts/interface_phase1_v1`（包括 48 个训练 NPZ）、失败 phase2、成功 `artifacts/interface_phase2_v2_clockfix`（含权重）、完整 diagnostic（含 204 个 NPZ）。这些、`.venvs`、`build/devel/install`、长日志、论文和缓存不提交。原输入 evidence 中没有任何 `.pt` 或 `.npz`。

## clockfix 的真实身份迁移

没有独立迁移脚本；正式迁移实现在原 clockfix 的 `run.py::collection_source_compatibility`，允许以下三项变化，其他源码、配置、构建产物与数据文件继续严格验证。它记录到**新 phase2** 的 provenance，不修改 phase1。

| 文件 | 迁移前 SHA256 | 迁移后 SHA256 |
|---|---|---|
| `ros_io.py` | `20e22059ebadc2b3e041c82b0d8f079ffe14bcafcbbe755acbd32d560175ad20` | `8efb3c230897e495544dfde5fd06af47b4f816a7386b087a5ab1bdae67ec9725` |
| `run.py` | `35c1a287a9ade18488a6f208eac8e37a2c636a2278aac54c179ad5d77511bd4b` | `8dcd1a98a5c9f9d7f03bdc805c7f203f09c6a4f7432de86d7de17cdaab188ce5` |
| `tests/test_startup.py` | 不存在 | `93c30d42ca7883124dc88e2320dfed8173ffbe6a01cabe27106667a0bea71152` |

理由是 `rospy.init_node` 前等待 `/use_sim_time=true`，初始化后等待正 `/clock`，仅在启动阶段重试 `SensorsNotReady`。不放宽运行时反馈阈值、不替换时间戳、不改变训练、动作或物理语义。本次用原函数处理 phase1/成功 phase2 两份历史 manifest，返回值与成功证据中的 `clock_startup_hotfix_v1` 记录完全相同。

成功 phase2 的 `collection_sha256` 与 phase1 的 240 项数据清单完全相同；本地可实际校验其中 192 个文件，48 个 NPZ 未提供。诊断证据对成功 phase2 的 report/provenance 哈希也与 `第三次_evidence` 对应文件完全一致。两份 source_sha256 相同，共 336 项；本地匹配 296 项，40 项服务器构建产物待服务器验证。

## 原件缺口与身份边界

- 需要补齐与三个源码目录及审计目录对应的四个**原始 ZIP**，才能核验容器 SHA256 与原始成员路径。已知目标包括 `hydrone-interface-diagnostic-v1.zip`、`hydrone-diagnostic-audit-20260917.zip`；前两个 ZIP 的精确原文件名仍应以实际补件为准。
- 四组 evidence 的原 ZIP 均未提供。旧 audit.json 引用的原诊断 ZIP SHA256 是 `ac537c9f5c489d93ea5a601796a73f5126136ddf6aa88a217d709a5b473ba21e`，此处仅引用已有审计。
- 本次临时重打包包含 626 个文件，SHA256 为 `9e967c9fd2077c3cd7787dfec569d88295be302e44cf4f2c0c6a34d20c9d5b37`。审计通过 612 个 manifested 回合文件，允许省略的 204 个 NPZ；这个新容器不提交 Git。
- 原成功权重应仍在 `artifacts/interface_phase2_v2_clockfix/models/seed_0/model.pt`。诊断 provenance 记录其 SHA256 为 `83f8c9646a29d8ade96ac7a327b6923694eace9767fda0a769b0e16a4e5728ea`；本次没有权重字节，不能声称已独立验证该权重。
- 本次可核实的源码集成与日志重算已完成，原 ZIP 容器身份、权重/NPZ 和服务器构建验证尚不完整；没有凭记忆重写缺失补丁或伪造任何哈希。
