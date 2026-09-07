# Hydrone ICRA 2021 DDPG / SAC

ROS Noetic + Gazebo Classic 11 下的 Hydrone 空水跨介质训练工作区。包含 Hydrone、RotorS、UUV Simulator、mav_comm 的源码快照与本地适配，支持 DDPG/SAC 的 Stage 1 和 Stage 2 独立实验。

**服务器部署入口：[tools/server/README.md](tools/server/README.md)。** 面向 Ubuntu 20.04、Python 3.8；阿里云 PAI DSW PPU 保留镜像厂商版 PyTorch，禁止用 NVIDIA CUDA wheel 覆盖它。本机 NVIDIA Conda 配置不能用于 PPU。

## 当前训练入口

`src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl/launch/icra2021_paper.launch`

推荐使用 `tools/server/run.bash`，它生成解析后的配置、独立输出路径、源码校验、运行环境记录和结果校验。默认只有 100 步；完整训练必须显式传 `--full`。四个实验共享 ROS/Gazebo 默认端口，顺序运行。

观测为 26 维：20 束 LaserScan、前一物理动作 3 维、水平/垂直目标朝向、目标距离。动作是机体前向速度、垂直速度、偏航增量，物理范围分别为 [0,0.25]、[-0.25,0.25]、[-0.25,0.25]。不是 RGB-D/CUPRL 的图像训练实现。

Stage 1：1000 回合；Stage 2：2500 回合；每回合最多 500 步。实际配置及论文/公开代码差异见 [REPRODUCTION_NOTES.md](src/hydrone_deep_rl_icra/REPRODUCTION_NOTES.md)。代码能运行与论文指标复现是两项不同验收，不能仅凭短测试保证收敛。

## 源码与许可证

四个依赖仓库已完整纳入 `src/`，不是需要额外拉取的 git submodule；包括模型、网格和本机已有修改。`provenance/source-repositories.json` 记录上游 URL、原提交和本地修改状态；`provenance/source-sha256.json` 记录发布时文件内容。上游 README 中的旧环境路径不作为本仓库部署入口。

各目录保留原许可证及文件版权声明，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。这是源码快照仓库，不保留嵌套上游 `.git` 历史。编译输出、Conda/venv、日志、checkpoint、缓存及迁移备份不进入 Git；排除清单见 provenance。

本地工作树位于 `/home/chenke/hydrone_ws`；服务器克隆后所有新输出路径由脚本基于实际仓库目录生成。不要复制本机 build/devel 或旧虚拟环境到服务器。
