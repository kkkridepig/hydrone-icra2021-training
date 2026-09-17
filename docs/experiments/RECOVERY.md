# 换电脑取回模型与实验数据

本分支通过 Git LFS 保存原始交付 ZIP 和完整运行备份；代码正常检出，模型/数组从归档恢复。已有权重和数据可以取回，无需为了恢复文件重新训练。恢复文件不等于新机器上的仿真环境已验收。

## 保存了什么

[归档清单](../../archives/experiments/2026-09-17/manifest.json) 中有 5 个 ZIP，总计 178929393 字节，约 170.6 MiB。Git 提交保存 LFS 指针，大文件实际存储于该 GitHub 仓库的 LFS 存储。需要 `git lfs pull` 获得内容；不能把网页下载的源码 ZIP 或只含 LFS 指针的 checkout 当作完整备份。

| 归档 | 内容 |
|---|---|
| `artifacts.zip` | 完整 1789 个运行文件，包括下面的 5 个模型、372 个 NPZ、配置、provenance、日志、预览和四组 evidence ZIP；失败 phase2 同样保留 |
| `hydrone-interface-pilot-v1.zip` | 原初始两阶段源码、patch、manifest、说明 |
| `hydrone-phase2-clockfix-v1.zip` | 原工程修复源码、patch、manifest、说明 |
| `hydrone-interface-diagnostic-v1.zip` | 原冻结诊断源码、patch、manifest、说明 |
| `hydrone-diagnostic-audit-20260917.zip` | 原只读审计、报告、JSON 和图 |

四个源码 ZIP 的成员字节与已集成版本来源完全一致；不要再将初始包覆盖到现有代码。根目录新收到的四份 `evidence*.zip` 与备份内四个 evidence 完全相同，没有另存重复副本。逐成员 hash 见 [原包清单](2026-09-17/original-zip-inventory.json) 和 [运行产物清单](2026-09-17/supplement-inventory.json)。

恢复后的模型路径如下，以新工作区为根：

| 路径 | 用途 |
|---|---|
| `artifacts/interface_phase2_v1/models/seed_0/model.pt` | 首次 phase2 的已训练模型；其评估启动失败，不能当成功来源 |
| `artifacts/interface_phase2_v2_clockfix/models/seed_0/model.pt` | 原成功 phase2 模型；冻结诊断的正确来源 |
| `artifacts/interface_diagnostic_v1/frozen/model.pt` | 上述成功模型的冻结副本 |
| `artifacts/server/ddpg_stage1_seed0_20260907T085154643124Z/checkpoint.pt` | DDPG Stage 1/seed0 历史 checkpoint |
| `artifacts/server/sac_stage1_seed0_20260908T012832492338Z/checkpoint.pt` | SAC Stage 1/seed0 历史 checkpoint |

三个界面模型 SHA256 均为 `83f8c9646a29d8ade96ac7a327b6923694eace9767fda0a769b0e16a4e5728ea`。恢复工具只读取字节，不执行 pickle 或加载 torch。

## 新电脑下载和恢复

准备 Git、Git LFS 和 Python 3.8 或更高版本。恢复工具只使用 Python 标准库；下载/恢复不需要 ROS、torch、GPU 或 PPU。以下命令在一个允许新建 `hydrone_ws` 的父目录执行，不能已有同名目录：

```bash
git lfs version
git clone --branch experiment/interface-mechanism-audit --single-branch \
  https://github.com/kkkridepig/hydrone-icra2021-training.git hydrone_ws
cd hydrone_ws
git lfs install --local
git lfs pull --include="archives/experiments/2026-09-17/*.zip"
git lfs fsck
python3 tools/interface_archive/restore.py --verify-only
python3 tools/interface_archive/restore.py --destination artifacts
```

Windows 上使用可用的 `python` 替代 `python3`。恢复完成后打印 `run_files_verified: 1789`、`model_files: 5`；`simulator_build_validated: false` 表示没有验证新仿真构建。恢复路径剥离原 ZIP 中的 `mnt/workspace/hydrone_ws/artifacts/` 前缀，直接得到当前工作区的 `artifacts/interface_phase1_v1` 等目录，不会写入旧服务器的绝对路径。

已有 checkout 可以先 `git fetch origin experiment/interface-mechanism-audit`，在新的隔离 worktree 审查当前分支，再按 [部署说明](DEPLOYMENT.md) 保留原改动。`--destination` 必须不存在，即使空目录也拒绝；不要先 `mkdir artifacts`。已有数据时指定新的路径，例如 `--destination ../hydrone_restored_artifacts`。恢复中断或失败会保留部分目录与实际错误，应另选新路径重试，不覆盖它。

工具先核验 5 个容器的 SHA256，再验证成员集合、大小、路径、类型、CRC 和逐文件 SHA256。发现只有 LFS 指针、缺文件、路径穿越、符号链接、大小写冲突或哈希变化时停止。外层 ZIP 和清单必须一起保存，不能用临时重打包容器替换原 ZIP。

完整归档会占用 Git LFS 的存储与下载额度。完成下载后，可在自己的持久化盘保留这 5 个 ZIP 和清单作为离线备份，避免反复下载；不要关闭哈希检查。GitHub LFS 不替代自己的第二份备份。

## 可以继续哪些工作

- **查看结果和离线审计**：恢复后即可读取旧数据。在已有 numpy/matplotlib 环境用 `tools/interface_analysis/audit.py` 重算，见部署说明；不需要重训。
- **原界面模型的推理/冻结诊断**：模型已保存。原 `learning.py` checkpoint 只有配置、`event_state` 和 `action_state`，没有 optimizer/epoch 中途续训状态。运行 phase2 入口会重新训练；需要使用冻结诊断入口才能复用现有模型。新机器运行诊断还须通过下述构建身份验收。
- **DDPG/SAC 续跑**：现有 `tools/server/run.bash --resume` 可恢复同算法、同阶段的未完成 checkpoint，包括优化器/replay/RNG；Gazebo 世界状态不在 checkpoint。提供的两次 Stage 1 均已完成目标 1000 回合，`tools/server/run.py` 会报 `Checkpoint already reached the configured episode target`。不能承诺对它们直接执行 `--resume --full` 就增加训练，也不能改成 Stage 2 冒充续跑。本次没有更改训练目标或添加新训练模式。

## 原构建无法提供时的边界

用户已确认无法提供原服务器 40 项 `devel` 构建产物，本次不再把它们列为等待用户补件的阻塞；其历史路径/hash 仍保留在 [validation-results.json](2026-09-17/validation-results.json) 的 `provenance.missing_server_build_files`。两项系统 Gazebo 插件的原字节也未提供。

新机器应按原服务器说明安装 Ubuntu 20.04/ROS Noetic、构建源码，并使用正确的加速器环境。PPU 必须保留镜像 vendor torch，不得安装普通 CUDA wheel 替换，不升级 CUDA/驱动。Windows 可用于保存与离线分析，不能据此声称已验证 Linux Gazebo/PPU。

原冻结诊断比较成功 phase2 的完整源码/构建哈希。新编译可能改变身份并触发 `Sources/build changed since successful phase2`；保存了模型仍不能自动通过这个检查。**不要删除检查或编辑旧 provenance/data hash**。本次没有原构建、没有新服务器访问，无法完成新构建的闭环验收，也没有创建跨构建迁移例外。未来需在新服务器核验构建差异和模型兼容性，并另行记录新环境与验证结果；旧记录和模型保持原样。

这种环境缺口不要求丢弃或重新学习现有权重。当前交付保证原模型/数据可下载并逐字节恢复，尚不保证任意新机器可以立即复现原 Gazebo 闭环。
