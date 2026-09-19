# 本地预验证环境

此目录提供独立的 Ubuntu 20.04、Python 3.8、ROS Noetic、Gazebo Classic 11 用户态，用于上传服务器前发现代码与环境问题。默认镜像没有 torch。源码只读挂载，构建产物写入容器临时目录，检查结果写入独立目录；不会启动研究训练或正式 56/168 回合实验。

PPU 厂商 torch、驱动和正式实验始终属于 **L3 server-only validation**。这个镜像不能用来配置或替换阿里云服务器的 Python/torch 环境。容器成功也不代表 PPU、历史服务器二进制身份或研究结论已验证。

实际机器版本、执行命令及成功/失败记录见 [LOCAL_VALIDATION.md](../../docs/experiments/LOCAL_VALIDATION.md)。本文件描述可重复使用的方法。

## 从 WSL2 执行

在 Docker Desktop 中为所用 WSL2 distribution 开启 integration，并确认使用 Linux containers。在 WSL2 Bash 中进入这个独立 checkout，例如：

```bash
cd /mnt/f/hydrone_train/local_validation
docker version
bash dev/local_validation/run.bash build-image
```

如果 WSL 报 localhost 代理不能映射、Docker Hub 鉴权超时，而 Windows 系统代理可用，可在 Windows 的 PowerShell 中使用备用构建入口：

```powershell
python -X utf8 dev/local_validation/build_windows.py
```

它只把构建请求交给同一个 Docker Desktop Linux 引擎，使用现有系统代理并补齐 Docker credential helper 的进程 PATH；不在主机安装 Python 依赖。完成后回到 WSL 执行下文测试。构建日志也保存在 `_runs/`，不会输出代理配置或凭据。

Ubuntu 默认 HTTP 源不稳定时，可加 `--ubuntu-mirror https://mirrors.ustc.edu.cn/ubuntu`；WSL 构建使用对应环境变量 `HYDRONE_UBUNTU_APT_MIRROR`。该选项只替换 Ubuntu 下载地址，保留 APT 签名和包哈希校验，不替换 ROS 源。APT 下载使用 BuildKit 缓存并最多执行三轮安装，失败包可继续补取。CPU target 应使用相同镜像参数，以复用已经完成的基础层。

仓库也可放入 WSL 的 Linux 文件系统。容器内 catkin 在 `/tmp` 构建，因此不会把 `build/devel/install` 写入仓库。无需恢复大型历史归档就能运行 L0/L1；归档及模型恢复仍按 [RECOVERY.md](../../docs/experiments/RECOVERY.md) 操作。

RotorS 原有构建会重写源码树中的 `models/iris/iris.sdf`，因此 `l2` 先在容器 `/tmp` 复制完整 `src/`，只允许该临时副本可写。`source-before.json` 和 `source-build-effects.json` 记录构建产生的字节变化，并验证只读仓库原文件保持不变；不修改历史 provenance。

镜像基于固定 digest 的官方 ROS Noetic Focal 镜像。APT 依赖使用镜像配置的签名软件源，具体系统包版本记录在 `/opt/local-validation-info/dpkg.tsv`；Python 包记录在同目录的 `pip-base.txt`。基础镜像固定不等于所有未来 APT 下载逐字节固定，保留每次运行记录的 image ID 以识别实际环境。

## 四级验证

| 等级 | 内容 | 可用命令 |
|---|---|---|
| L0 Static | 实际 Python 3.8 AST 编译、bash 语法、Git whitespace、JSON/XML/YAML、42 个 ROS package manifest、交付源码哈希 | `bash dev/local_validation/run.bash static` |
| L1 Unit | 纯逻辑、契约、启动回归、诊断报告、归档恢复、验证工具自身测试 | `bash dev/local_validation/run.bash unit` |
| L2 Linux/ROS smoke | ROS imports、catkin 构建、生成消息导入、项目 launch 解析、xacro 展开、有限时长空世界 Gazebo smoke | `ros`、`gazebo`、`l2`，见下文 |
| L3 Server-only | PPU 算子/runtime、云服务器驱动、正式 Gazebo/PPU 集成、长训练与研究实验 | 本工具没有 L3 执行模式 |

逐项执行并检查返回码，保留失败日志：

```bash
bash dev/local_validation/run.bash static
bash dev/local_validation/run.bash unit
bash dev/local_validation/run.bash ros
bash dev/local_validation/run.bash gazebo
bash dev/local_validation/run.bash l2
```

`ros` 只做 imports；`gazebo` 是空世界基础 smoke，确认真实 ROS/Gazebo 时钟和服务，不加载研究策略；`l2` 编译 catkin 实际发现的 41 个 package（另有 `catkin_tools_prebuild`，共 42 个构建目标），再检查生成消息、项目 launch/xacro，最后运行空世界 smoke。第 42 份 package manifest 属于原本含 `CATKIN_IGNORE` 的 ROV 教程包，没有解除它的忽略规则。这些检查不代替 Hydrone 全传感器/Lee 控制链的正式服务器验收。构建有 30 分钟上限，Gazebo 检查有短超时并清理本次创建的进程。不要把 ROS 节点清单解析成功记为节点全部启动成功。

默认容器运行时无外部网络、无设备透传、无特权，限制 CPU、内存与进程数。默认资源为 2 CPU、6 GiB，可在调用前设置 `HYDRONE_LOCAL_CPUS`、`HYDRONE_LOCAL_MEMORY`。构建镜像下载系统包时需要联网。

## 可选 CPU 网络测试

现有 [初始交付验证记录](../../tools/interface_experiment/LOCAL_VALIDATION.md) 已明确采用过与服务器隔离的 CPU torch 测试。本目录据此提供独立 `cpu-torch` target，固定 `torch==2.4.1+cpu`，适配 Python 3.8，不包含 CUDA/PPU runtime。

```bash
bash dev/local_validation/run.bash build-cpu-image
bash dev/local_validation/run.bash unit-cpu
bash dev/local_validation/run.bash ros-cpu
```

Windows 代理备用入口的可选 CPU 构建命令为 `python -X utf8 dev/local_validation/build_windows.py --cpu-torch`。

若容器访问 PyTorch 官方下载站出现 TLS EOF，而 Windows 现有 localhost 代理可访问，使用 `--forward-local-proxy` 把该代理显式转交给构建步骤。本机实际命令为：

```powershell
python -X utf8 dev/local_validation/build_windows.py --cpu-torch --ubuntu-mirror https://mirrors.ustc.edu.cn/ubuntu --forward-local-proxy
```

转发通过 Docker Desktop 的 `host.docker.internal`，不新开代理端口，不关闭 TLS；带内嵌凭据的代理会被拒绝。预定义 proxy build arguments 不写入最终镜像环境，构建记录中的代理值会遮蔽。

本机 Python3.8 下 pip24 的 TLS 请求失败，而镜像内系统 pip 的同一 HTTPS 请求通过。因此 CPU 构建使用系统 pip **仅下载**固定 wheel，然后由隔离环境 pip 离线安装；不向系统 Python 安装 torch。官方 PyTorch CPython3.8/Linux amd64 wheel 的 SHA256 固定在 requirements 中，所有下载 wheel 的实际哈希另存 `/opt/local-validation-info/cpu-wheels.sha256`。

仅这个显式选择会安装、导入 CPU torch。默认镜像若意外发现 torch，`unit` 将拒绝继续，避免混用环境。CPU 选择会验证 `+cpu` 版本标记、无 CUDA/HIP runtime 且无可用 CUDA 设备。即使 CPU 前向/反向/checkpoint 测试通过，也必须在 PPU 上重新验证厂商实现。

项目已有测试的范围为：

- 默认无 torch：79 项项目测试；`test_learning.py` 的 2 项真实跳过，报告保存跳过原因。
- CPU 选择：额外 15 项 CPU 网络测试，包括上述 2 项学习测试；夹具数据仅验证软件行为。
- 验证工具自身另有 8 项测试，单独列为 `validation_harness`，不与项目研究证据混淆；其中进程归属检查需 Linux `/proc`。
- `test_agent_runner.py` 的 5 项需要 ROS imports 加 torch，由 `ros-cpu` 单独执行并记录为 L2。其环境仍是测试夹具，不是真实 Gazebo 实验。

上述是当前文件盘点数量；执行报告从 `unittest.TestResult` 获取真实测试计数，不用预期数量填补缺失测试。每个 suite 单独启动 Python 进程，避免 `test_contracts.py` 等重名模块互相污染。每个子进程默认 120 秒超时；超时、导入错误、零测试或缺失结果均为失败。

## 输出与失败含义

每次启动创建 `dev/local_validation/_runs/<UTC时间>-<PID>-<mode>/`，保存镜像信息、源提交和工作树状态、`console.log`、`exit-code.txt`。L0/L1 另有 `checks/report.json`、逐命令日志以及逐 suite 的真实结果。`_runs/` 已忽略，不提交长日志、临时 checkpoint 或构建结果。

脚本也支持在独立 Linux Python 环境直接运行：

```bash
python3 dev/local_validation/checks.py --root . \
  --output logs/local_validation/static-new --level static
python3 dev/local_validation/checks.py --root . \
  --output logs/local_validation/unit-new --level unit
```

输出目录必须为空，已有结果不能覆盖。未在 Python 3.8 解释器上运行时，L0 会明确将 runtime 检查记为失败；其他 Python 的 3.8 grammar 解析不能冒充 Python 3.8 验收。

L0 全仓当前可检测到已有的 4 个问题，保留原始源码等待单独授权修复：

| 文件 | 问题 |
|---|---|
| `src/rotors_simulator/rotors_evaluation/src/rosbag_tools/analyze_bag.py:53` | Python2 异常语法 |
| `src/uuv_simulator/uuv_tutorials/uuv_tutorial_dp_controller/scripts/tutorial_dp_controller.py:93` | Python2 print 语法 |
| `src/uuv_simulator/uuv_tutorials/uuv_tutorial_rov_model/urdf/rov_example_base.xacro:135` | XML 注释内部含 `--`，不合法 |
| `src/uuv_simulator/uuv_tutorials/uuv_tutorial_rov_model/urdf/rov_example_snippets.xacro:93` | XML 注释内部含 `--`，不合法 |

因此全仓 `static` 可返回非零；这些失败不会被白名单改成通过。报告另列 `python_core`（tools、ICRA2021 模块/测试/入口及本验证工具），便于定位与当前入口的关系。XML 解析只验证语法，不能替代 ROS xacro 展开、插件加载和模型行为。

`original_delivery_source_hashes` 按 pilot → clockfix → diagnostic 的实际覆盖顺序核对最终源码，再核对 audit 工具，共 25 个文件；不重复应用补丁，也不修改历史 manifest/provenance。该检查未涵盖缺失的原服务器 `devel` 字节身份。

`stored_audit_consistency` 只比较仓库内原审计和历史重算 JSON，允许浮点尾差；它不是本次对 evidence ZIP 的重算，更不是新 Gazebo 结果。真实审计重算必须提供原 evidence ZIP 并明确记录输入哈希。

PPU 实际更新、模型在厂商 torch 下的加载、正式仿真重现、性能与长期稳定性，仍按 [DEPLOYMENT.md](../../docs/experiments/DEPLOYMENT.md) 在服务器验收。不要将本地 `devel` 与历史服务器产物等同，也不要关闭原模型/源码身份检查来复用数据。
