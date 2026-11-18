# Legion Module 契约 v1（草案）

> 状态：草案（v1）。冻结后如需变更契约，递增 `sdk` 版本并在本文记录。
> 适用范围：挂载到 nanobot-legion 网关上的业务模块。第一个实现：`nanobot-quant`。

---

## 1. 定位

| 层 | 职责 | 不负责 |
|---|---|---|
| **cloud-agent-gateway（CAG）** | 平台底座：OAuth、通道绑定、会话、持久卷、文件、relay | 任何业务概念 |
| **nanobot-legion（Legion）** | 运行时：agent 生命周期、gatekeeper、侧边栏与置顶会话渲染、文件、**模块路由挂载点**、凭证注册与渲染 | **具体业务有哪些页面、叫什么、状态怎么算** |
| **Legion Module（模块）** | 业务：自己的页面、自己的配置、自己的任务与产物 | 别人的模块、网关内部实现 |

Legion 只认识「谁声明了页面」，不认识「量化」「期权」「图片」。

> 与上游 nanobot **CLI Apps** 区分：CLI Apps 是给 agent 用的工具插件；Legion Module 是挂到网关上的业务界面 + 服务。两者不互相替代。

---

## 2. 契约对象

模块是一个 pip 包，提供一个模块级对象：

```python
class Module:
    name: str            # 唯一标识，^[a-z][a-z0-9_-]{1,31}$
    display: str         # 置顶会话标题，如 "📊 业务管理"
    sdk: int = 1         # 契约版本；Legion 支持范围外一律拒绝加载
    route_prefix: str    # URL 命名空间，如 "/apps/image"；历史模块可为 "/config"

    def register_routes(self, app, ctx) -> None:
        """注册本模块的 FastAPI 路由。异常即视为加载失败（见 §3）。"""

    def sections(self, ctx) -> list[Section]:
        """返回置顶会话内容的结构化描述，由 Legion 渲染成统一格式。"""

    # ── 可选能力（未实现 = 不提供，不算契约破坏，不升 sdk）──────────
    def mcp_specs(self, ctx) -> dict[str, object]:
        """返回要注入 agent config 的 MCP server 声明（键=server 名）。

        - 每次启动由 squad_config_sync 聚合一次；必须幂等、无副作用
        - ``ctx.gatekeeper`` 可能为 None（sync 是独立进程）
        - 跨模块重名 → 先到先得并告警
        """
```

> **spec 形状**：Legion 按属性名读取（duck typing，不 import 模块的类型）：
> `name`（必填，须等于字典键）、`command` / `args`（必填）、`target_agents`（必填，
> 空列表=不注入任何 agent）；可选 `tool_timeout`、`env`、`env_provider_keys`、
> `env_provider_model_keys`。
>
> **导入纪律**：模块 plugin 的**顶层**只允许 stdlib 与轻依赖；重依赖（pandas /
> lumibot / 交易所 SDK…）必须放在 `register_routes` / `sections` / `mcp_specs`
> 函数体内 —— `squad_config_sync` 会在启动早期 import 每个模块的 plugin 模块。

### 2.1 渲染原子

只允许以下两类结构，**模块不得返回预拼好的 Markdown**（保证样式统一、渲染层可替换）：

```python
Section(heading: str, rows: list[Row], desc: str = "")
Row(label: str, desc: str = "", status: str = "", link: str = "")
```

- `status`：一句状态值（如「⚪ 实盘关闭」），由**模块自己**计算
- `link`：站内路径（如 `/config/exec`）或绝对 URL

### 2.2 ctx（只读）

| 字段 | 说明 |
|---|---|
| `data_root` | 持久卷根目录 |
| `instance_path(agent)` | 指定 agent 的实例目录 |
| `agents` | 当前 roster 的 agent 名单 |
| `data_dir(module)` | 模块私有状态目录（`legion/<module>/`）|
| `files_dir(module)` | 模块产物目录（`files/<module>/`）|
| `log(msg)` | 写网关日志（可见、带模块名前缀）|

---

## 3. 发现与加载

- 唯一来源：Python entry point group `nanobot_legion.modules`
- **无任何隐式兜底**：不按包名/约定名推导模块（曾经的 `nanobot_quant.legion_plugin` 兜底已于 2026-11-18 删除）——一个新部署只需装自己想装的业务系统，不会被旧系统牵连
- 加载顺序：核心路由先注册，模块按 `name` 字典序
- **失败隔离**：单个模块 import / 注册 / 渲染抛错 → 记可见日志并跳过该模块，核心与其他模块不受影响
- **sdk 不兼容 → 拒绝加载该模块**（fail-closed）并打醒目日志，不做「尽力加载」

---

## 4. 命名空间

- 新模块必须使用 `/apps/<name>/*`（允许模块内部自定义子路径）
- `nanobot-quant` 历史路径 `/config/*` 保持不动（书签、文档、链接不断）
- 启动时检测前缀冲突：后加载者拒绝注册并告警，不允许两个模块抢占同一前缀
- 模块不得注册到其他模块的前缀下

---

## 5. 界面

- **每个模块一个置顶会话**，标题取 `display`，统一挂在「系统配置」项目下，排在核心会话之后
- 会话内容 = 核心区块（状态/工具入口，由 Legion 提供）+ 本模块 `sections()` 渲染结果
- 模块**不直接读写** session / sidebar 文件，由 Legion 统一写入（复用现有 pinned chat 机制）
- 会话在网关启动时重建（与现行为一致）

---

## 6. 依赖与隔离

- 模块之间**零 import**；需要联动走 relay / agent 委派
- 目录约定：配置 `credentials/<module>/`、状态 `legion/<module>/`、产物 `files/<module>/`
- 模块自读自写自己的目录；**Legion 不解析任何业务配置**（状态一律由模块算好交给渲染层）
- agent 归属：模块只能**建议**需要哪些 agent（README / 面板提示），**不得自动写入 `squad_config.json`**——agent roster 的唯一权威是配置文件与用户操作

---

## 7. 后台任务

- 允许模块在网关进程内跑**轻量**后台任务，但必须同时满足：
  1. 不阻塞事件循环（长耗时走线程 / 独立进程）
  2. 可停（提供停止路径，不强杀）
  3. 异常不外溢（不拖垮网关）
  4. 状态可见（页面或日志可判断在跑 / 停了 / 出错）
- **重任务**（长耗时、高 CPU、频繁网络）应放在模块自己的 agent 进程内，由 agent 生命周期托管
- 现状说明：`nanobot-quant` 的 TD / 期权循环常驻网关进程，属**技术债**，后续单独迁移

---

## 8. 版本与兼容

- 当前契约版本 `sdk = 1`
- Legion 升级契约时递增该值，并在本文「变更记录」中写明破坏点
- 过渡安排：Legion 在一版内保留旧的内置白名单（插件优先、发现到即跳过白名单），下一版本删除白名单
  - **已完成（2026-11-17，步 4）**：旧白名单与业务会话兜底内容均已删除；未发现模块时网关不报错、只是没有业务页面与「业务管理」会话（stderr 提示 `未发现 Legion Module`）。
  - **已完成（2026-11-18，零隐式依赖）**：三处旧业务系统的隐式依赖全部删除——① `module_loader` 的约定名兜底 ② `squad_config_sync` 的 `nanobot_quant.mcp_spec` 兼容回退（现无模块声明钩子即跳过注入）③ `tools/squad_delegate.py` 对 `nanobot_quant.onchainos_cli` 的可选 import（连带删掉从未随委派下发的 `variables`/`pre_fetch` 死代码）。至此 legion 源码对任何具体业务包零引用，`grep -r nanobot_quant src/` 应为空。

### 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 1 | 2026-11-17 | 首版：Module 对象、Section/Row 原子、entry point 发现、命名空间、失败隔离；同日完成路由插件化、会话 sections() 渲染与旧白名单下线 |
| 1.1 | 2026-11-18 | 新增**可选**钩子 `mcp_specs(ctx)`：模块可声明自己的 MCP server（Legion 不再硬编码 `nanobot_quant.mcp_spec`）；可选能力不升 `sdk`，旧模块不受影响 |
| 1.2 | 2026-11-18 | **零隐式依赖**：删除 entry point 约定名兜底、`nanobot_quant.mcp_spec` 兼容回退、`squad_delegate` 对 `nanobot_quant.onchainos_cli` 的可选 import；无模块时仅告警不报错；`run_research_swarm` 返回体不再带 `pre_fetch` 字段（此前只作回显、从未随委派下发） |

---

## 9. 非目标（v1 明确不做）

- 模块清单 / 注册中心文件（用 pip 安装 + entry point 即可）
- 模块间依赖解析与编排
- 模块启用开关（装了即显示；卸载靠镜像依赖调整）
- 网关内的模块生命周期托管（启停、监督、重启）

---

## 10. 验收标准

**第二个模块接入时，Legion 零改动。** 若被迫修改 Legion，说明契约缺失——补契约并升 `sdk`，而不是给该模块开例外。
