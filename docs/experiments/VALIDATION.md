# 迁移与离线验证记录

日期：2026-09-17。本次实际执行环境为 Windows、Python 3.12.0、NumPy 2.5.2、Git for Windows 2.53.0。绘图使用仓库外临时 venv 中的 matplotlib 3.11.2。未安装 torch，未连接或更改 PPU 服务器，未启动 Gazebo 训练。

## 原源码 ZIP 与可迁移归档

最新收到的四个源码/审计 ZIP 已核验 SHA256、路径/类型/重复/加密成员、CRC，并隔离解压；40 个成员全部与首轮原目录一致，补丁未重复应用。机器清单见 [original-zip-inventory.json](2026-09-17/original-zip-inventory.json)。根目录四个新 evidence ZIP 也分别与完整备份内对应原件 hash 相同，未保存重复副本。

根据用户保存模型以换电脑继续实验的新授权，新增限定目录的 Git LFS 归档与 `tools/interface_archive/restore.py`。原五个归档共 178929393 字节，保存 5 个 `.pt`、372 个 NPZ 与完整运行记录；历史三个验证/输入 JSON 保持原样。原 40 项 `devel` 已确认无法提供，作为环境缺口记录，不再等待用户补件。

新增恢复测试实际运行 `python -m unittest discover -s tools/interface_archive/tests -v`：8 项全部通过，覆盖原字节恢复、已有目录/文件防覆盖、路径穿越/Windows 路径别名、大小写/文件目录冲突、符号链接、成员缺失、hash 错误和 LFS 指针识别。`restore.py --verify-only` 实读校验 5 个原容器、40 个源码文件和 1789 个运行文件通过，不加载 checkpoint。

本地另向全新目录实际恢复并独立逐文件复核，1789 项 hash、5 个 `.pt` 和 372 个 NPZ 均匹配。5 个暂存 LFS 指针的 oid/size 与原容器一致；归档中 1075 个文本成员的高置信凭据模式检查无发现；25 个导入源文件和既有历史快照字节未变。新增 2 个 Python 文件通过 Python3.12 的 Python3.8 grammar 检查，54 个文档相对链接及 12 个 bash 代码块语法通过，`git diff --check` 通过。详细记录见 [archive-validation.json](2026-09-17/archive-validation.json)。

归档恢复和原模型重用的能力边界见 [RECOVERY.md](RECOVERY.md)。界面模型没有 optimizer/epoch 续训状态；DDPG/SAC 的两次 Stage 1 已达到 1000 回合目标；缺原构建时冻结诊断的旧身份检查仍可能拒绝新环境。本次不将下载/恢复测试记作 ROS/PPU 验收。

## 补件后的实际验证

后续输入为 `artifacts.zip` 和 `artifacts/mnt/workspace/hydrone_ws/artifacts/`。本轮只补充原件/数组/模型字节核验、原 ZIP 审计和记录，不修改受 provenance 监控的运行源码；首轮源码契约测试结果保留，未为累计通过数重复执行。机器结果见 [supplement-validation.json](2026-09-17/supplement-validation.json)，文件清单与容器身份见 [supplement-inventory.json](2026-09-17/supplement-inventory.json)。

| 本轮检查 | 实际结果 |
|---|---|
| 仓库状态与远端 | 本轮开始工作树干净；重新 fetch 后 main 仍为 `8790a9e`，工作分支仍为首轮的 `fca44f2`，继续原草稿 PR |
| 原容器安全性 | 外层 artifacts.zip 加四个 evidence ZIP，共 5 个；路径穿越、重复/大小写冲突、链接/特殊文件、加密成员检查无异常；隔离解压并全量读取通过 CRC |
| 完整归档身份 | 外层 ZIP 为 178519746 字节，SHA256 `1262e30830fae7041c8dc7ef153433bf5f27b31669691a2088737d68758d0060`；1825 成员中有 1789 文件，与提供的解压目录全部逐字节一致 |
| 四组 evidence 对应 | 206/15/495/626 个成员分别与首轮四组 evidence 的文件集合/hash 完全一致；无需重套源码补丁 |
| 历史原诊断 ZIP | 实算 SHA256 为 `ac537c9f5c489d93ea5a601796a73f5126136ddf6aa88a217d709a5b473ba21e`，吻合历史 audit.json，不再只是历史引用 |
| 原模型字节 | 首次 phase2、成功 clockfix phase2 和 diagnostic frozen 三个模型均为 237757 字节，SHA256 `83f8c9646a29d8ade96ac7a327b6923694eace9767fda0a769b0e16a4e5728ea`，与 provenance 相同 |
| 完整数据 manifest | phase1 的 240 项全部匹配，含 48 个 NPZ；diagnostic 的 816 项全部匹配，含 204 个 NPZ |
| NPZ 结构与实读 | `np.load(..., allow_pickle=False)` 读取全部 372 个 NPZ；维度/dtype/有限值、元数据 steps 与日志长度全部通过 |
| 图像与预览 | 48/120/204 个 NPZ 分别含 4969/14773/20981 帧，均为 `(N,48,48,3)` uint8；372 张 PNG 预览与数组选帧逐像素一致 |
| phase1/phase2 数值对应 | 共 19742 行；actions/heights/labels 与真实逐步日志完全一致；input_dt 与前一时刻一致；history_rows 由原函数从 sensors/前一动作重算一致 |
| diagnostic NPZ 内容边界 | 原代码只保存 images；204 份 NPZ 的动作/标签核对仍依赖原 JSONL，不能冒称 NPZ 包含这些字段 |
| 原 ZIP 审计 | 使用已入库的原 audit.py 直接处理 diagnostic 原 evidence.zip；36 校准＋168 测试、20 次失败，计数/选择/查询/事件统计与历史一致 |
| server DDPG/SAC | 各 1040 条记录＝1000 training＋40 deterministic_evaluation；训练 episode 序号完整；步数实算 DDPG 177443、SAC 497866，与 summary/validation 一致 |
| checkpoint 使用边界 | 5 个 `.pt`（界面三份、DDPG/SAC 两份）均计算 hash；没有反序列化、执行推理、重训或复验梯度 |

372 个 NPZ 中，phase1/成功 phase2 包含 images、history_rows、heights、labels、sensors、actions、input_dt；diagnostic 仅包含 images。首次失败 phase2 没有评估 NPZ，与其 0 回合结果一致。上述检查是完整性与接口一致性验证，不证明相机物理正确性、策略性能或新实验结果。

本轮实际审计命令（从仓库根目录，以已有独立 numpy/matplotlib 环境执行）：

```bash
python tools/interface_analysis/audit.py \
  ../artifacts/mnt/workspace/hydrone_ws/artifacts/interface_diagnostic_v1/evidence.zip \
  ../.migration-work/supplement-20260917/audit-original-zip
```

[原 ZIP 重算 JSON](2026-09-17/audit-original-zip-recomputed.json) 的 input_sha256 已与历史结果相同；其余差异只剩三个距离值各 `2.7755575615628914e-17` 的浮点尾差。所有方法计数、查询数、事件头计数与研究结论不变。没有用 FixtureEnv 或合成数组冒充真实回合。

补件中没有 `.py`/`.patch`/`.bash`/`PACKAGE_MANIFEST.json`，属于产物备份，因此没有新增源码应用步骤。全部运行源码/既有 manifest 与首轮提交字节保持一致，原件本身不入 Git；本轮只提交三个派生 JSON 及 README/四份文档更新。源码交付 ZIP 和服务器构建身份的剩余缺口见文末。

本轮最终审查：25 个导入文件的工作树/Git 对象 hash 与原包一致；首轮 1382 个输入文件、本轮 1789 个输入文件及外层 ZIP 未变；历史清单/验证/重打包审计快照未改写。36 个相对 Markdown 链接、更新文档中的 11 个 bash 代码块语法和分支内 12 个 JSON 解析均通过。8 个变更文件为 UTF-8 无 BOM、LF；`git diff --check` 通过，高置信凭据模式检查无发现，无单文件超过 1 MiB。没有改变 `tools/`、`src/` 或 provenance。

## 首轮已执行结果（补件前）

| 检查 | 结果与边界 |
|---|---|
| Git 基线/状态 | 隔离新 clone 初始干净；main=`8790a9ea1598c779e38e1fa5eeb3eeec6ca87bcf`，与旧基线相同；未发现仓库 AGENTS.md |
| 输入目录安全 | 8 个目录、1382 个文件；无符号链接/路径越界；没有原始 ZIP，不能声称检查过原 ZIP 成员 |
| 包 manifest | 初始 14 源文件＋patch，clockfix 3＋patch，diagnostic 9＋patch 全部 SHA256 匹配 |
| 补丁检查 | 三个原 patch 分别先 `git apply --check` 成功，再应用一次；应用结果与完整文件逐字节一致 |
| 初始包原测试 | `python -m unittest discover -s tools/interface_experiment/tests -v`：20 项发现，18 通过、2 跳过 |
| clockfix 后原测试 | 同命令：33 项发现，31 通过、2 跳过；其中新增 `test_startup.py` 的 13 项全部通过 |
| diagnostic 原测试 | `python -m unittest discover -s tools/interface_diagnostic/tests -v`：17 项通过、0 跳过 |
| 最终去重测试数 | 两个最终套件共 50 项：48 通过、2 跳过；不能把重复执行的原 18 项重复计数 |
| Python3.8 语法 | 16 个 Python 文件通过 Python3.12 的 `ast.parse(..., feature_version=(3, 8))`；**不是 Python3.8 运行验证** |
| bash 语法 | Git Bash `bash -n` 检查两个实验 `run.bash` 与主文档中的 10 个 bash 代码块，通过；没有 source 服务器环境 |
| CLI 帮助 | 实验 run.py、diagnose.py、audit.py 的 `--help` 均退出 0 |
| 诊断启动失败路径 | 真入口指向不存在的 phase2，退出 2、状态 INCOMPLETE；错误/报告/evidence 均保留，未启动仿真 |
| 输出防覆盖 | 同一失败输出目录再次运行被拒绝，原 report.json hash 不变 |
| 原 phase1 报告 | 在副本上用原 report.py 重算，48/48，gate 与原记录完全相同 |
| 首次 phase2 报告 | 在副本上重算，0/120、INCOMPLETE，与原记录一致；不是教师 0% 的实测结果 |
| 成功 phase2 报告 | `第三次_evidence` 副本重算 120/120、各方法 24/24、PILOT_RESULTS_REQUIRE_REVIEW |
| phase1 数据身份 | 对实际存在的 192 项逐文件核验通过；原 manifest 另列 48 个未提供的 NPZ；phase2 的 240 项 collection_sha256 与原 manifest 一致 |
| clockfix 迁移 | 用原 compatibility 函数核验两份历史 source_sha256，返回值与成功 phase2 的迁移记录完全一致 |
| 源码身份 | 成功 phase2 与 diagnostic 的 source_sha256 一致，336 项中本地存在的 296 项全部匹配；另外 40 项为 devel 构建产物，未本地验证 |
| diagnostic 源码 | 8 项受监控源码 hash 与真实 diagnostic provenance 一致；包含 README 的 9 个交付文件另与包 manifest 一致 |
| phase2→diagnostic 引用 | 成功 phase2 report/provenance 的字节 hash 与 diagnostic 所记录的两个来源 hash 完全一致 |
| 原 audit.py 重算 | 36 校准＋168 测试；612 个 manifested 文件全部匹配；204 个 NPZ 按原规则省略；204 回合 steps 均匹配 |

`test_learning.py` 中的 `test_actual_gradient_update` 与 `test_short_fit_and_checkpoint_roundtrip` 因本机没有 torch 而跳过。没有为了增加通过数安装普通 torch；应在既有服务器 vendor 环境补跑。

以上单元测试的模拟 ROS/FixtureEnv/合成数组只检查软件契约，不属于任何 Gazebo 实验成功证据。48/48、120/120 和诊断数值来自用户提供的真实逐回合文件，来源没有混用。

## 首轮重打包审计的身份（历史过程保留）

原 `audit.py` 原样执行，输入是将收到的 diagnostic evidence 目录按原相对路径打包的新 ZIP。打包前检查路径与链接，打包后检查 626 个成员、重复路径和 CRC。它是临时适配容器，不提交 Git、不声称是上传原件。

```text
历史审计引用的原 ZIP SHA256（仅引用）：
ac537c9f5c489d93ea5a601796a73f5126136ddf6aa88a217d709a5b473ba21e

本次重打包 SHA256（本地实算）：
9e967c9fd2077c3cd7787dfec569d88295be302e44cf4f2c0c6a34d20c9d5b37
```

实际命令等价于：

```bash
python tools/interface_analysis/audit.py diagnostic-evidence-repacked.zip new-audit-output
```

结果见 [audit-recomputed.json](2026-09-17/audit-recomputed.json)。与 [原 audit.json](2026-09-17/audit-original/audit.json) 比较，差异仅为 `input_sha256` 和三个距离值约 `2.8e-17` 的浮点尾差；方法计数、查询数、事件头分母/计数、选择档位与失败分类全部一致。原图被保留，重算也成功生成图；不要求不同 matplotlib/NumPy 环境生成的 PNG 字节一致。

重算确认：teacher/short 各 24/24；long/geometry/unified/separated/oracle 各 20/24；168 回合全部 crossed；20 次任务失败全部为 seed201 空入水的 height_boundary。专家查询依次为 short2303、long660、geometry1117、unified1441、separated1403、oracle1127。separated 运动注入窗口为 4/49，clean 报警 214/650。含义和局限见 [研究状态](RESEARCH_STATUS_20260917.md)。

原脚本的 612 项 hash 校验覆盖 manifest 列出的回合文件；其余顶层配置/日志的本次身份在 input-inventory.json 中记录，不把新生成 manifest 当成独立的历史签名。

## 提交前检查与处理

三轮有实质变化的检查分别对应初始包、clockfix 与 diagnostic 集成。源码均保持原件字节，没有为通过测试重写 provenance、删除身份检查、改变奖励/阈值/物理/动作语义或自动格式化。

新增两份派生 JSON 首次 `git diff --check` 因 Windows CRLF 报尾部空白；只将本次生成的 `audit-recomputed.json` 与 `input-inventory.json` 保存为 LF，再检查通过。原 Python/JSON/launch、原历史报告及包 manifest 没有换行转换。

首轮最终检查包含 `git diff --check`、提交范围/大文件/凭据模式检查、文档相对链接核验，以及提交对象和 checkout 的原件 SHA256 对照。源目录 `src/`、`tools/server/` 和既有 provenance 未修改；没有纳入 venv、构建产物、模型、训练数组、长日志或重复 ZIP。[validation-results.json](2026-09-17/validation-results.json) 是首轮验证快照，其中“权重不可用”等字段保留当时状态；本轮已补齐的部分以 supplement-validation.json 为准。

## 未执行或待补齐

- **Python3.8 真实运行、ROS/Gazebo、PPU 算子/梯度、相机/施力/时钟服务时序、完整闭环重跑**：当前是 Windows 离线环境，且本任务未授权自动启动长时训练。部署后的服务器验证按 [DEPLOYMENT.md](DEPLOYMENT.md) 执行。
- **40 项原构建产物、两个系统 Gazebo 插件**：用户已确认无法提供原 `devel`；也没有两个原系统插件字节。保留历史 hash 与环境缺口，不能宣称当前服务器构建已验证，不再等待原 devel 补件。
- **模型运行而非模型字节**：原 model.pt 和 frozen 副本的字节/hash 已核验；本地仍无 torch，尚未加载权重、做 CPU/PPU 推理或反事实检查。原两项网络测试依然是跳过，没有安装普通 torch。
- **源码/模型/数据原件**：四个源码/审计 ZIP、四组 evidence 原 ZIP、完整模型与全部 372 个 NPZ 均已补齐并核验；现通过 LFS 归档保存，不再列为缺失文件。
- **56 回合、真值/学习高度对照、到达后继续 10 秒**：仅提案，没有代码入口、运行或通过记录。

本次联网读取 ROS Noetic `simtime.py` 确认初始化时依据 `/use_sim_time` 选择时钟的代码背景；[研究状态](RESEARCH_STATUS_20260917.md) 所列五篇近邻的 arXiv 题名/摘要也重新读取。它们不是服务器验证的替代。
