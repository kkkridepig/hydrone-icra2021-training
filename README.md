# Hydrone ICRA 2021 DDPG / SAC

ROS Noetic + Gazebo Classic 11 下的 Hydrone 空水跨介质训练工作区。包含 Hydrone、RotorS、UUV Simulator、mav_comm 的源码快照与本地适配，支持 DDPG/SAC 的 Stage 1 和 Stage 2 独立实验。

**服务器部署入口：[tools/server/README.md](tools/server/README.md)。** 面向 Ubuntu 20.04、Python 3.8；阿里云 PAI DSW PPU 保留镜像厂商版 PyTorch，禁止用 NVIDIA CUDA wheel 覆盖它。本机 NVIDIA Conda 配置不能用于 PPU。

原 DDPG/SAC 发布检查见 [2026-09-07 验证报告](docs/VALIDATION_20260907.md)。新增界面实验的历史服务器证据与本次离线复核见下文；它们不代表 DDPG/SAC 已复现论文指标。

## 界面机制实验与证据整理（2026-09-17）

已按原始文件哈希纳入两阶段实验、ROS 仿真时钟修复、冻结模型诊断和只读审计。当前实现为小 CNN、8 步运动历史 MLP、H=4 动作 MLP、masked L1 监督与规则执行调度；低层 Lee 闭环持续工作。

- [部署与数据保留](docs/experiments/DEPLOYMENT.md)：PPU 环境、已有服务器迁移和实际运行命令。
- [研究状态](docs/experiments/RESEARCH_STATUS_20260917.md)：实验结果、负结果、局限与尚未执行的下一步。
- [交付物与提交映射](docs/experiments/ARTIFACT_INVENTORY.md)：实际收到的解压目录、文件 SHA256、补丁顺序及缺失原件。
- [本次验证记录](docs/experiments/VALIDATION.md)：实际通过、跳过及未执行的检查。
- [换电脑恢复模型与数据](docs/experiments/RECOVERY.md)：Git LFS 下载、完整归档校验、防覆盖恢复，以及续跑的实际边界。
- [两阶段入口](tools/interface_experiment/run.bash)、[冻结诊断入口](tools/interface_diagnostic/run.bash)、[离线审计](tools/interface_analysis/audit.py)。

真实日志离线重算确认：phase1 为 48/48，clockfix 后 phase2 为 120/120；追加诊断为 36 校准＋168 测试，teacher/short 各 24/24，其余五种策略各 20/24。全部跨界不等于任务成功，当前没有证实 separated 的稳定性优势。到达后继续运行 10 秒的 56 回合方案仍是提案。

`artifacts.zip`、四个原始 evidence ZIP、原模型及 372 个 NPZ 已完成离线核验；诊断 ZIP 与模型哈希均吻合历史 provenance，并已直接用原 ZIP 重算审计。四个源码/审计交付 ZIP 也已补齐，与此前交付目录逐字节一致。DDPG/SAC 两次 Stage 1 记录各完成 1000 个训练回合，只能说明运行完成，不能据此证明收敛或论文复现。

本次没有启动 ROS/Gazebo/PPU 训练。根据保留关键实验产物的新要求，完整备份与四个源码原包保存在 [Git LFS 归档目录](archives/experiments/2026-09-17/)，包含 5 个 `.pt`、372 个 NPZ、配置和原日志。用 [恢复工具](tools/interface_archive/restore.py) 取回到新目录；原构建产物缺失仍限制新机器上的冻结诊断验收。原包 README 中的“交付前未运行”等表述按历史原件保留，当前入口说明以新增部署文档为准。

## 当前训练入口

`src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl/launch/icra2021_paper.launch`

推荐使用 `tools/server/run.bash`，它生成解析后的配置、独立输出路径、源码校验、运行环境记录和结果校验。默认只有 100 步；完整训练必须显式传 `--full`。四个实验共享 ROS/Gazebo 默认端口，顺序运行。

观测为 26 维：20 束 LaserScan、前一物理动作 3 维、水平/垂直目标朝向、目标距离。动作是机体前向速度、垂直速度、偏航增量，物理范围分别为 [0,0.25]、[-0.25,0.25]、[-0.25,0.25]。不是 RGB-D/CUPRL 的图像训练实现。

Stage 1：1000 回合；Stage 2：2500 回合；每回合最多 500 步。实际配置及论文/公开代码差异见 [REPRODUCTION_NOTES.md](src/hydrone_deep_rl_icra/REPRODUCTION_NOTES.md)。代码能运行与论文指标复现是两项不同验收，不能仅凭短测试保证收敛。

## 源码与许可证

四个依赖仓库已完整纳入 `src/`，不是需要额外拉取的 git submodule；包括模型、网格和本机已有修改。`provenance/source-repositories.json` 记录上游 URL、原提交和本地修改状态；`provenance/source-sha256.json` 记录发布时文件内容。上游 README 中的旧环境路径不作为本仓库部署入口。

各目录保留原许可证及文件版权声明，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。这是源码快照仓库，不保留嵌套上游 `.git` 历史。编译输出、Conda/venv、缓存和新运行输出继续忽略；本次经核验的历史备份仅通过上述限定目录的 Git LFS 归档保存，排除清单见 provenance。

本地工作树位于 `/home/chenke/hydrone_ws`；服务器克隆后所有新输出路径由脚本基于实际仓库目录生成。不要复制本机 build/devel 或旧虚拟环境到服务器。
