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
```

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

- 首选：Python entry point group `nanobot_legion.modules`
- 兜底：按发行包名推导约定模块（如 `nanobot_quant.legion_plugin`）
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

### 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 1 | 2026-11-17 | 首版：Module 对象、Section/Row 原子、entry point 发现、命名空间、失败隔离 |

---

## 9. 非目标（v1 明确不做）

- 模块清单 / 注册中心文件（用 pip 安装 + entry point 即可）
- 模块间依赖解析与编排
- 模块启用开关（装了即显示；卸载靠镜像依赖调整）
- 网关内的模块生命周期托管（启停、监督、重启）

---

## 10. 验收标准

**第二个模块接入时，Legion 零改动。** 若被迫修改 Legion，说明契约缺失——补契约并升 `sdk`，而不是给该模块开例外。
