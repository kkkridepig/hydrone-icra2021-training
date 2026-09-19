# 本地预验证环境与执行记录

日期：2026-09-19 至 2026-09-20（Asia/Shanghai）。本次仅做环境工程和预检查，未修改研究算法、奖励、动作语义、物理参数、成功阈值或历史 provenance。L0/L1/L2 不代表 L3 已验证。

## 代码身份与输入审计

- GitHub 实时分支盘点中，实验分支 `experiment/interface-mechanism-audit` 的最新提交为 `73ddbedd53c4161746497620365310b96a346bc3`；`main` 为 `8790a9ea1598c779e38e1fa5eeb3eeec6ca87bcf`。
- 原 checkout `F:/hydrone_train/repository` 保留不动。新 clone 为 `F:/hydrone_train/local_validation`，工作分支 `dev/local-validation-20260919`。克隆设置 `GIT_LFS_SKIP_SMUDGE=1`，未下载本检查不需要的大型历史归档。
- 仓库不存在 `AGENTS.md`。已遵循会话提供的全局指令。
- 用户指定的 `docs/research/HYDRONE_RESEARCH_AND_CODE_REVIEW_20260917.md` 不在该提交中；实际阅读的是附件 `F:/hydrone_train/HYDRONE_RESEARCH_AND_CODE_REVIEW_20260917.md`，不把它冒充仓库文件。
- 已阅读根 README、现有实验部署/恢复/验证文档、服务器安装/构建/PPU 环境脚本、requirements、setup.py、package.xml、CMakeLists.txt、现有 tests 和主要 launch/xacro。
- Windows Git 初始继承了 `core.autocrlf=true`。只在新 clone 设置 `core.autocrlf=false`，核对 1,422 个已跟踪文件，将仅由 checkout 转换的 1,240 个文件恢复为提交中的原始字节，再刷新 Git 索引；未变更任何已提交源码内容。最终交付哈希检查另核对 25 个实验/诊断/审计源文件。

## A. 主机、WSL 与 Docker 实测

| 项目 | 实际值 |
|---|---|
| Windows | Windows 11 家庭版 x64，build 26200.9457 |
| Windows Python / Git | 3.12.0 / 2.53.0.windows.1 |
| WSL | 2.7.14.0 |
| WSL2 kernel | 6.18.33.2-microsoft-standard-WSL2 |
| 默认 distribution | `RflySim-20.04`，WSL1；未转换、未设为验证环境 |
| 验证使用的 distribution | `Ubuntu`，WSL2，Ubuntu 22.04.3 LTS，用户 k |
| WSL Python / Git | 3.10.12 / 2.34.1；不是目标 Python 3.8 |
| Docker Desktop | 本次安装 4.91.0 (239619) |
| Docker Linux Engine / CLI | 29.8.0 / 29.8.0，linux/amd64 |
| containerd / runc | 2.3.4 / 1.4.3 |
| Docker 磁盘 | `F:/hydrone_train/.local-validation-state/DockerDesktopWSL` |
| Docker WSL integration | 只启用 `Ubuntu`，不启用默认 WSL1 distribution |

审计时未找到已安装的 Docker，随后执行：

```powershell
winget install --id Docker.DockerDesktop --exact --source winget --accept-package-agreements --accept-source-agreements --silent
```

winget 校验安装器哈希后安装成功。没有重新安装 WSL，没有升级 CUDA/驱动，没有操作远端 PPU。

C 盘剩余约 8 GB，因此在拉取镜像前，通过 Docker Desktop 当前版本的设置接口迁移 WSL 磁盘到 F 盘，实测新位置存在 `disk/docker_data.vhdx` 和 `main/ext4.vhdx`。设置读取与写入返回 HTTP 200；迁移后 Windows 与 `wsl -d Ubuntu -- docker version` 均能连接 Linux 引擎。原设置备份保留在工作区之外的本地 `.migration-work` 目录，不提交机器配置。

Docker 初次恢复时遇到残留 UNIX socket 的 `The file cannot be accessed by the system`。在确认 Docker 进程停止后，保留并重命名新安装产生的临时 socket 目录，再启动恢复成功；没有 factory reset、删除镜像数据或终止其他 ROS/WSL 工作负载。桌面自动化工具出现 `failed to write kernel assets`，故未依赖 UI 点击结果，而是读取本机 Docker 前端实现和实际设置 API 后操作并验证。

## B. 仓库实际结构

`src/` 有 1,324 个文件，包含 `hydrone_deep_rl_icra`、`mav_comm`、`rotors_simulator`、`uuv_simulator` 四大源码树，共 42 份 package manifest。catkin 实际发现 41 个包，另加 `catkin_tools_prebuild` 共 42 个构建目标；ROV 教程包原有 `CATKIN_IGNORE` 保持不变。盘点到 91 个 package.xml/CMakeLists.txt 和 240 个 launch/world/test/xacro 资源。

已有 `tools/interface_experiment/`、`tools/interface_diagnostic/`、`tools/interface_analysis/`、`tools/interface_archive/`；服务器安装、构建和 PPU 检查在 `tools/server/`，环境参考在 `tools/environment/`。本次只新增 `dev/local_validation/` 和本文，不修改这些既有入口。

## C–F. 四级测试边界

| 等级 | 测试与依赖 | 解释 |
|---|---|---|
| L0 Static | 容器 Python 3.8 AST、bash、Git whitespace、JSON/XML/YAML、package manifest、原交付哈希 | 发现语法、配置和身份变化；其他 Python 的 grammar 解析不等同 3.8 实跑 |
| L1 Unit | experiment/diagnostic/archive 契约、启动回归、报告，以及 6 个 ICRA 纯逻辑测试文件 | 不需要 ROS、Gazebo、PPU；默认没有 torch |
| L1 可选 CPU | `test_learning`、checkpoint、DDPG、network shapes、SAC | 只在独立 CPU torch 镜像中执行；合成夹具不代表真实实验 |
| L2 ROS | ROS imports、`test_agent_runner`（还需 CPU torch）、catkin、生成消息、launch 解析、xacro | runner 测试中的环境仍是假环境；不宣称已运行真实机器人 |
| L2 Gazebo | 有界空世界 gzserver，真实 `/clock` 推进和 `get_world_properties` | 验证基础 ROS/Gazebo 安装，不代表 Hydrone 物理/传感器/Lee 全链成功 |
| L3 Server-only | 厂商 PPU torch/runtime、驱动、真实算子/梯度、正式 Gazebo/PPU 集成和长实验 | 本工具无 L3 执行模式，必须回服务器验证 |

默认 L1 为 79 项通过的项目测试、2 项因缺 torch 跳过的学习测试，加 8 项验证工具测试，共运行 89 项。CPU 模式的项目测试共 94 项，加工具测试 8 项，共 102 项。`test_agent_runner` 的 5 项单列 L2。

## G. 原有环境能力与新增方案

原提交没有 Dockerfile 或 devcontainer。已有 `tools/server/install_ros.bash`、`build.bash`、setup/PPU 脚本及环境文档可作依赖参考，但没有在主机直接运行这些安装器。`src/hydrone_deep_rl_icra/requirements.txt` 含未固定的 torch，本地没有安装整份 requirements。

新增 Docker 基础固定为官方 `ros:noetic-ros-base-focal` 的 digest：

```text
sha256:72b8bc59035dc0a5b8e07aae28c16caa84192971d72d207c72ed734fb1d5e97d
```

实际拉取成功，镜像内 ROS 软件源使用 `snapshots.ros.org/noetic/final/ubuntu`。没有禁用 APT 签名验证。Ubuntu/ROS 的 EOL 状态意味着此环境用于兼容性检查，不等同持续维护的生产系统。

默认 target 无 torch。现有 `tools/interface_experiment/LOCAL_VALIDATION.md` 明确允许历史隔离 CPU 测试，因此提供显式 `cpu-torch` target，使用适配 Python 3.8 的 `torch==2.4.1+cpu`；不把历史 Python3.12 的 2.5.1+cpu 强装到 3.8。镜像记录系统包和 Python 包版本，运行记录 image ID；APT 依赖并非全量逐包固定。

测试容器仓库只读、无网络、无设备透传、非特权、非 root，并限制为 2 CPU/6 GiB/512 进程。catkin 使用容器 `/tmp` 中的完整源码副本，因为 RotorS 原构建会重写源码树内的 SDF；记录副本构建前后身份，并核对原仓库字节不变。结果写入每次独立的 `_runs/`，不覆盖已有数据，长日志和编译产物不进入 Git。

首次 WSL 构建在 `auth.docker.io` 匿名鉴权处超时。Docker 显式使用现有 Windows 系统代理后，WSL 客户端仍受 localhost 代理不可达影响；Windows 客户端补齐 credential helper PATH、使用现有系统代理后成功下载同一固定基础镜像。`build_windows.py` 是此问题的可复用回退入口；只提交 Linux 容器构建请求，不安装主机 Python 依赖。

## 实际执行与结果

以下为实际运行结果。日志路径均相对于 `dev/local_validation/_runs/`，日志保留在本机且被 Git 忽略。

| 项目 | 当前实际结果 |
|---|---|
| Windows Python3.12 预审计 L1 | 88 运行、86 通过、2 因无 torch 跳过，0 失败/错误 |
| Windows 语法/配置预审计 | 核心 Python grammar、29 个 bash、22 个 JSON、53 个 YAML、42 package、25 源码哈希通过；不是 Python3.8 运行 |
| 全仓已有失败 | 2 份 Python2 脚本、2 份非法 XML 注释；源码保留 |
| Docker 默认与 CPU 镜像 | 均构建成功；Python3.8.10、ROS Noetic、Gazebo11.15.1、catkin_tools0.9.4、Git2.25.1；CPU 镜像为 torch2.4.1+cpu，无 CUDA |
| 容器 L0 | 返回 1：216 个 Python 文件中 2 个旧文件失败，核心 54 个全部通过；349 个 XML 中 2 个旧文件失败；29 bash、22 JSON、53 YAML、42 package manifest、25 原源码哈希及 git diff 检查通过 |
| 容器 L1 默认 | 89 运行、87 通过、2 因无 torch 跳过，0 失败/错误 |
| 容器 L1 CPU | 102/102 通过，0 跳过 |
| L2 ROS imports | 通过，实际 Python3.8.10 |
| L2 ROS + CPU runner | 5/5 通过；仍为夹具环境 |
| L2 独立空世界 Gazebo | 通过，约 5.289 秒；51 个时钟样本从 0.001 推进到 0.039，world service success=true |
| L2 全流程 | 返回 0；42/42 构建目标成功，9 个有警告，编译耗时 11 分 7.3 秒；生成消息导入、项目 launch 节点解析、实验机器人 xacro 展开及空世界 Gazebo 全部通过 |
| L3 | 未执行，server-only |

结果位置：

- L0：`20260919T172053Z-36170-static/checks/report.json`。
- 默认 L1：`20260919T171956Z-35133-unit/checks/report.json`。
- CPU L1：`20260919T173526Z-40526-unit-cpu/checks/report.json`。
- ROS imports：`20260919T171956Z-35243-ros/console.log`。
- ROS + CPU：`20260919T173633Z-41021-ros-cpu/agent-runner.json`。
- 独立 Gazebo：`20260919T172017Z-35819-gazebo/gazebo/result.json`。
- L2 全流程：`20260919T173303Z-39757-l2/`。`source-build-effects.json` 确认原仓库不变，临时副本最终字节变化为 0（SDF 生成规则执行后内容一致）。Gazebo 记录 28 个时钟样本，0.032 → 0.046，服务成功，约 5.672 秒退出。

构建警告包括 Gazebo Classic EOL 提示、protobuf 未声明 syntax、UUV C++ 非 void 函数缺少返回值和 xacro 重定义 `pi`。本次保留这些旧源码问题；编译通过不证明这些路径运行正确。没有启动项目机器人全部节点，没有运行原包的完整 rostest/Gazebo 回归或训练。

最后另在默认镜像的实际 Python3.8 中对 5 个新增 Python 文件执行 `ast.parse`，对 3 个新增 bash 文件执行 `bash -n`，均通过；Windows 执行 `git diff --check`、`git diff --cached --check` 及两份文档相对链接检查均通过。修改范围检查确认仅涉及本次验证目录和本文。

实际默认镜像 ID 为 `sha256:2378abcecc38853a65868497e2494f5b880feb09fcd9efbfe9473e5788b3b08b`；CPU 镜像 ID 为 `sha256:f93043c66316e7d982caa5a7f6887d4b9dd6124c5ea837b37dfdded031a897a6`。每次运行的 `image.json` 另保留完整身份。

已发生并保留记录的失败及处理：

- Docker Hub token 超时、Windows credential helper PATH 缺失、APT HTTP 502/临时 DNS 故障：采用 Windows 构建入口、缓存重试与保留签名校验的 USTC Ubuntu HTTPS 镜像。尝试替换 ROS 源遇到签名 key 不匹配后撤回，最终保留原官方 snapshot 源。
- Python3.8 下 venv pip24 下载出现 TLS EOF：系统 pip 仅下载固定 CPU wheel，再由隔离 venv 离线安装；未向主机或系统 Python 安装 torch，未关闭 TLS。
- 初次 catkin 参数 `--parallel-jobs 2` 不受支持，已改为 `--parallel-packages 2`。
- 第二次 catkin 在只读源码上生成 RotorS SDF 失败：20 个构建目标成功、1 失败、21 abandoned，日志 `20260919T172151Z-36842-l2/`。已改为容器临时源码副本，不修改研究实现或历史 provenance。
- 辅助 upstream 镜像缺少 Git 导致一次静态预检失败；正式 L0 使用完整默认镜像。并行启动 WSL 时曾出现 `Wsl/Service/0x8007274c` 和瞬时 cwd 错误，串行重试成功。

全仓四处原有问题：

- `src/rotors_simulator/rotors_evaluation/src/rosbag_tools/analyze_bag.py:53`：Python2 异常语法。
- `src/uuv_simulator/uuv_tutorials/uuv_tutorial_dp_controller/scripts/tutorial_dp_controller.py:93`：Python2 print。
- `src/uuv_simulator/uuv_tutorials/uuv_tutorial_rov_model/urdf/rov_example_base.xacro:135`：XML 注释包含 `--`。
- `src/uuv_simulator/uuv_tutorials/uuv_tutorial_rov_model/urdf/rov_example_snippets.xacro:93`：同类 XML 错误。

`stored_audit_consistency` 只比较已提交的两份历史审计 JSON，未在本次重新分析 evidence ZIP。任何 FixtureEnv/合成数组/checkpoint 单测都没有记为 Gazebo 成功。

## H. 与正式服务器的差异及保留项

- 容器用户态目标为 Ubuntu20.04/Python3.8/Noetic，但内核是 WSL2；不是服务器原内核、驱动或设备。
- checkout 位于 Windows NTFS，挂载权限、符号链接和文件系统语义仍与原服务器有差异；catkin 使用 Linux 容器临时副本只消除了构建输出直接写入 NTFS 的影响。
- 无 PPU、厂商 torch、云镜像补丁或厂商算子实现；普通 CPU torch 不能证明 PPU 行为。
- 使用软件渲染、有限 CPU/内存，不能代表服务器渲染、吞吐、时序、实时性或长期稳定性。
- 本地重新编译不能重建无法提供的原服务器 40 项 `devel` 产物字节身份。不修改历史 provenance，不关闭模型或源码哈希检查。
- 本次不载入研究模型，不恢复历史训练数据，不运行 56/168 回合，不提出或实现新算法。权重/归档迁移沿用 [RECOVERY.md](RECOVERY.md)。
- 未启动 `lee_controller.launch`；盘点发现它引用不存在的 `hydrone_deep_rl.launch`。launch 解析与空世界 smoke 不掩盖这个旧入口问题。

服务器仍必须执行厂商 runtime 与模型加载检查、真实前后向更新、Hydrone 相机/UUV 服务/复位/Lee 控制链集成、正式 Gazebo/PPU 实验。不要在服务器运行本地 Docker CPU requirements 来替换 vendor torch。

## 使用入口

详见 [本地环境 README](../../dev/local_validation/README.md)。在 Windows 的现有 localhost 代理条件下：

```powershell
cd F:/hydrone_train/local_validation
python -X utf8 dev/local_validation/build_windows.py --ubuntu-mirror https://mirrors.ustc.edu.cn/ubuntu
python -X utf8 dev/local_validation/build_windows.py --cpu-torch --ubuntu-mirror https://mirrors.ustc.edu.cn/ubuntu --forward-local-proxy
```

在 `wsl -d Ubuntu` 的 Bash 中：

```bash
cd /mnt/f/hydrone_train/local_validation
bash dev/local_validation/run.bash static
bash dev/local_validation/run.bash unit
bash dev/local_validation/run.bash unit-cpu
bash dev/local_validation/run.bash ros
bash dev/local_validation/run.bash ros-cpu
bash dev/local_validation/run.bash gazebo
bash dev/local_validation/run.bash l2
```

`static` 的全仓非零结果需按报告解释；不能仅为变绿修改受监控旧源码。最小首次 smoke 使用 `ros`；随后才运行限时 `gazebo`，最后评估 `l2` 全包构建。
