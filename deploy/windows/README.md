# Windows W1：独立运行时与双模型可行性

后续进展：2026-10-24已继续完成W2便携配置/新库首代及W3启停/维护的本机验证，当前入口见[W3说明](./W3.md)与[结果摘要](./w3-result.json)，前一阶段见[W2说明](./W2.md)；下文W1结果按2026-10-03历史范围保留，不表示正式ZIP已交付。

这是构建/验证工具，不是已经可供日常使用的 RAG 发布包。W2 配置/首代、W3 启停、W4 ZIP、W5 产品验收不在这些脚本的范围内。合同及实际结果见 [OPS-005](../../new_docs/运维/OPS-005-Windows单人本地发布与初始化方案.md#w1-result)、[DEV-012](../../new_docs/开发/DEV-012-服务托管与启动停止.md#w1-result)、[TEST-009](../../new_docs/测试/TEST-009-本地运行与回滚验证.md#w1-result)。

## 装配

在维护者的已验证 Windows CPython 3.14.0 构建环境运行；只读取当前环境和 HF 已缓存的固定模型，不修改它们。官方 Python 嵌入式 ZIP 下载到 `build/windows-w1/downloads/`，不用安装器。当前锁的 Python SHA-256 与官方 `.zip.sigstore` 声明摘要核对；没有独立完成签名验证，不能等同于密码学来源认证。

```powershell
.\.venv-gpu\Scripts\python.exe deploy\windows\prepare_w1.py assemble --python-zip build\windows-w1\downloads\python-3.14.0-embed-amd64.zip --output build\windows-w1\bundle
```

`--output` 必须为项目 `build/` 下的新子目录，存在时拒绝覆盖，也不会删除目录。输出含 `runtime/`、两份 `models/`、`locks/`、许可清单、`validation/`、`w1-manifest.json`；不带后端、前端、`.env`、个人文档/数据库、API Key 或私人题库。模型权重和运行时在被 Git 忽略的 `build/`，不能随代码提交。

首轮原型通过 `complete --python-zip <同一ZIP> --output <既有W1目录>` 完成运行支持资源装配：先验证既有清单，再带入嵌入式模块对应的标准库 `.py`（供 PyTorch 源码检查使用），以及六份固定的 app-local VC++ DLL。已有锁包含其哈希后，后续 `assemble` 自动回放并严格核对，不再隐式接受不同来源文件。`complete` 只处理受检查的 W1 原型与固定支持/验证文件，不升级业务数据。

`w1-lock.json` 固定完整运行依赖闭包、版本、资源数量/大小/聚合哈希、模型 revision 与逐文件哈希。装配检查原安装 `RECORD` 的 SHA-256，然后仅复制其登记的资源/动态库/元数据；跳过 venv 的 Scripts、`.pyc` 和 `.pth`，不复制 venv、`pyvenv.cfg` 或其解释器。不带独立测试/旧 Streamlit 根依赖；某些构建或训练相关包是当前依赖声明的传递依赖，首阶段保留，不做深度裁剪。解释器的相对 `._pth` 不启用 site、不读取全局 PYTHONPATH/用户包/注册表搜索路径。

这是 **installed-RECORD vendoring**，不是原始 wheelhouse：现有安装元数据没有记录完整原 wheel URL/哈希，不能声称能从任意仓库重新下载相同字节。原始来源认证、离线 wheelhouse、最终完整许可/NOTICE 审查在 W4 单列；不得把 installed payload hash 冒充 wheel hash。需要更新已审核版本时，显式执行下列命令并审查锁差异，不在用户电脑上运行：

```powershell
.\.venv-gpu\Scripts\python.exe deploy\windows\prepare_w1.py capture
```

VC++ 支持资源来自本机已安装、经本轮 Microsoft Authenticode `Valid` 核对的 x64 文件，装配只读取 System32，不往里面写入，也不执行 VC++ 安装器；实测进一步核对这些 DLL 确实从包内载入。该来源仅用于 W1 内部原型；公开分发前须按[微软再分发说明](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170)确认获许可的正规 Redist 来源与条款。W4 必须处理，不能因为“文件有微软签名”就当作获得了公开再分发许可。

## 实测

直接使用 **输出目录里的解释器**。它不会导入 backend 或读取生产 `.env`。先校验完整清单，使用三段公开合成中文资料，真实加载两模型，检查 1024 维/归一化/有限数值/简单相关性、15 条重排、实际所有参数设备与重排精度，记录首次/预热后耗时与内存。`top_k=5`仅记录部署合同，未运行应用最终上下文选择器。测试保留双模型同进程驻留；GPU 重排 FP16、CPU 重排 FP32，不通过关闭重排验证 CPU。

```powershell
.\build\windows-w1\bundle\runtime\python.exe .\build\windows-w1\bundle\validation\probe_w1.py --device cuda:0 --report gpu.json
.\build\windows-w1\bundle\runtime\python.exe .\build\windows-w1\bundle\validation\probe_w1.py --device cpu --report cpu-on-gpu-host.json
```

它还验证常用 API/解析/DLL 导入、合成 Chroma 持久化和真实 BM25S mmap 载入/检索，检查所有 Python 模块路径在包内。HF/PyTorch/分词缓存和临时文件在该验证目录内；Python 审计钩子禁止网络及已覆盖事件的包外写入。审计钩子不覆盖所有本地 DLL 的原生文件/注册表写入，因此 **不是 W5 的完整系统写入跟踪**；合成依赖检索不是实际应用 Hybrid/首代/质量/UI 验收。

复制/移动整个 bundle 到新的中文空格目录，旧 bundle 根不可访问时重复 GPU/CPU 测试，不能仅从旧目录引用模型。这里只验运行时/模型移动，不包含 W2 尚未实现的数据路径移动。

### 2026-10-03本机结果

本机GPU、同包CPU、移动后的GPU/CPU均完成，结果摘要见[w1-result.json](./w1-result.json)，详细范围见[TEST-009](../../new_docs/测试/TEST-009-本地运行与回滚验证.md#w1-result)。当前实际原型为`build/windows-w1/中文 空格 验证包/`；上文`bundle`是新装配示例目标，旧测试根已移走、不存在。完整原始报告在该原型的`reports/`，失败尝试保留，不覆盖为成功。

原型清单34,078文件、7,215,859,317字节（约6.72 GiB，未压缩，不含报告/缓存及清单自身）；两模型约3.60 GB。没有制作最终ZIP，不推断其压缩大小。移动复测同时设置无效`PYTHONHOME`、外部`PYTHONPATH`及无效`HF_HOME`，确认隔离解释器、模块、模型和VC运行库仍使用当前包内资产。

文件完整校验会预热操作系统缓存，`load_s`只是构造器耗时，探针`cold_s`实际为该进程第一次推理，并不是系统冷启动；GPU重排初始化部分工作在首次推理中完成。15候选是三段短资料重复，不代表实际长chunk、完整Hybrid时延、知识库回答质量或最低硬件规格。依赖警告（Jieba旧转义写法、Transformers缓存变量弃用）保留记录，当前固定版本不阻断验证；升级版本时须复验。

移动时发现内存映射信息仍带旧目录名，不能据此判断实际DLL借用。探针改用Windows模块句柄枚举及Unicode加载路径检查，每份匹配VC库仍须实际位于当前包内；实现依据[EnumProcessModules](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-enumprocessmodules)及[GetModuleFileNameW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-getmodulefilenamew)，不放宽包外检查。

## 无 NVIDIA、无驱动的 Windows 验证

在没有 NVIDIA 显卡和 NVIDIA 驱动的 Windows x64 电脑上复制整个 bundle，双击 `validation/验证无驱动CPU.cmd`，或运行：

```powershell
.\runtime\python.exe .\validation\probe_w1.py --device cpu --expect-no-driver --report no-driver-cpu.json
```

不需 Python、pip、Node、联网、API Key 或管理员权限。保留 `reports/no-driver-cpu.json`，成功须为 `status=passed`、`driver.nvcuda_loadable=false`、两模型实际 CPU、重排 float32。驱动仍可载入会明确失败；隐藏显卡、`CUDA_VISIBLE_DEVICES=-1` 或本机强制 CPU 不满足本用例。不修改/禁用本机驱动，不自动开启 Windows Sandbox/Hyper-V。

## 退出、清理与边界

验证命令退出即结束，不启动服务、不登记系统设置。结束后可以人工删除 **仅构建验证目录**；其中没有生产个人数据。不要删除整个仓库、已有 `.venv`、HF 全局缓存或 `data/`。两份模型许可声明分别为 MIT 与 Apache-2.0，原模型卡随文件保留；Python/依赖的随附许可保留在 runtime 和 inventory，不把清单当最终法律审核。
