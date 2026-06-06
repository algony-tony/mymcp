# mymcp 项目全面评估

- **Date**: 2026-06-06
- **Scope**: 架构 / 测试 / 文档 / 可观测性 / 安全 / 运维
- **Method**: 4 个并行 subagent 分别评估各维度，源码与文档逐项核对，本文件汇总
- **Status**: 评估结论；后续 plan 待基于本文档撰写

---

## 评分汇总

| 维度 | 评分 | 一句话定位 |
|---|---|---|
| 架构 / 代码质量 | **B** | 结构清晰、契约层有割裂，配置单例泄漏 |
| 测试 | **B+** | 真实变异测试 + 良好安全/边界覆盖，缺真服务端到端 + 覆盖率门禁 |
| 文档 | **B** | README/CLAUDE.md/规范文档高质量，被陈旧 `.env.example` 和 CHANGELOG 拖累 |
| 可观测性 | **A−** | 整个项目最强项：RED + 饱和度 + tracing + recorder 失败分类全到位 |
| 安全 | **C+** | 基础原语扎实，但 systemd unit 零硬化 + token 存储非原子 + `.env.example` 错前缀 |
| 运维 | **B** | CI 矩阵 + 变异测试 + release 自动化都不错，缺真 readiness 探针和断路器手动重置 |

整体大致 **B / B+**，属于"小而美、运维素养高于平均"的项目，主要扣分点集中在几个**易修但持续放血**的细节上。

---

## 跨维度的高杠杆问题（多个 agent 都指向的同一根因）

### 1. `.env.example` 仍是 `MCP_*` 旧前缀 —— 文档 + 安全双重红旗
- 代码读 `MYMCP_*`（`config.py:29`），example 用 `MCP_*`，操作员复制后所有设置被静默忽略，`MYMCP_ADMIN_TOKEN` 退回到启动期随机值。30 分钟可修，最优先。

### 2. Tool 返回值的"双形协议"已经长成中央 switch
- `call_tool` 必须区分 `{success: False, ...}` 与 `{exit_code, timed_out}` 两种形状（`mcp_server.py:178-199`），还要按工具名 fan-out 构造 `output_payload`（`mcp_server.py:214-240`）。
- 这个症状在多处显现：架构 agent 称之为"加一个新工具要改 4 处代码"，文档 agent 注意到这段关键代码**没有 WHY 注释**，测试 agent 注意到只能通过 mock 来覆盖。
- 根治方案：引入 `ToolResult` dataclass + 每工具的 `audit_params()` 钩子，让 `call_tool` 只做"记账 + 转发"。

### 3. TokenStore 每次请求都重写整个 JSON（且非原子）
- `validate()` 更新 `last_used` 后 `_save()` 同步重写文件（`auth.py:42-48`），既是**事件循环上的阻塞 I/O**（架构），又是**写放大 + 崩溃易损**（安全）。
- 立刻能做：写改成 `tempfile + os.replace`；`last_used` 改为内存态，定期 flush。

### 4. `bash_execute` 旁路 `check_protected_path` —— 文档承认 + 运行时无护栏
- 架构、文档、安全 3 个 agent 都指出来：rw token 可以 `cat audit.log`、`rm tokens.json`。
- shipped 的 `mymcp.service.in` **完全没有任何硬化指令**（`NoNewPrivileges` / `ProtectSystem` / `ReadWritePaths` / `CapabilityBoundingSet` 都没有）。这是单点 ROI 最高的一处加固。

### 5. Recorder 与 core 的耦合并非真正解耦
- `mcp_server.py:330-359` 硬写 `server_overview` 分支 + 延迟 import `RecorderSupervisor`；`wiring.py:64-108` 通过字符串 getattr 私有属性穿越 3 层；recorder 关闭时 `server_overview` 仍登记在 `READ_TOOLS` 中、返回 `RecorderDisabled`。
- 真要做"可插拔"，得引入 `ToolProvider` 注册表，recorder 包自己注册自己的工具。

### 6. CI 没有覆盖率门禁
- 测试与运维 agent 都点名：`--cov` 跑了、badge 也更新了，但**从不 `--cov-fail-under=N`**。变异测试都做了，反而最便宜的覆盖率门禁没装。

### 7. CHANGELOG 落后 3 周 + 两个 recorder 配置项未文档化
- `MYMCP_RECORDER_LLM_MAX_TOKENS`、`MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD`（`config.py:99-102`）在 README Configuration 表中缺失；PR #43/#45/#47/#48 都没进 CHANGELOG。

---

## 分维度精要

### 架构 (B)

**亮点**：
- stateless + contextvar 鉴权干净（`server.py:56-67` ↔ `mcp_server.py:113-116`）
- audit/observability 在 MCP 通道和 transfer 旁路通道**两边一致**（`transfer/endpoints.py:62-84`）
- recorder 失败分类 + cursor 回滚是教科书级的"at-least-once"
- `audit_output.py` 的 T1 摘要（sha + head/tail + size）防止敏感内容入审计日志
- `recorder/llm/factory.py` 的 lazy import 让 optional extras 真正可选
- `cmd_doctor` 直接读 `.env` 还原 systemd 视角，运营友好

**主要病灶**：
- `config.__getattr__` 历史属性 + 函数默认值在 import time 求值（`tools/files.py:63`, `tools/bash.py:88`）→ `reset_settings_cache()` 后这些默认值是**陈旧的**，测试用 `monkeypatch.setenv` 静默不生效
- 文件工具 / TokenStore 都在 async 路径里做同步 I/O
- 边界类型偏松（大量 bare `dict` 返回），mypy pass 是因为签名宽，不是因为类型严
- `_extract_params` 用关键字黑名单做敏感字段脱敏（`mcp_server.py:83-92`），未来新工具的 secrets 直接漏到 audit
- transfer 审计把 `role` 写死成 `"rw" if upload else "ro"` 并伪造 `tool="transfer_redeem"`，混淆"票据签发者"和"实际操作者"，且该工具名**不在 `MUTATING_TOOLS` 集合**——recorder 因此完全看不到文件上传
- `cli.cmd_serve` 用 `# noqa: SLF001` 直接戳 `TokenStore._lock`/`_data` 注入临时 rw token

### 测试 (B+)

**亮点**：
- mutmut 在 CI 真跑（smoke on PR + 5-shard full on master，`ci.yml:96-303`），有 mutation-score badge
- `test_clamping.py` 把每个边界都 `min-1/min/min+1/max/max+1/∞` 参数化
- `test_security.py` 真测了 `../`、symlink 逃逸、null byte、超大 header、CRLF token 注入
- `test_integration.py` 用 `ASGITransport` 走完整 middleware → permission → dispatch → JSON-RPC 链路
- recorder 子系统 ~3300 LOC 测试覆盖 1750 LOC 源码
- CI 矩阵覆盖 Py 3.11/3.12/3.13

**主要病灶**：
- 没有覆盖率 / 变异分数 floor
- 全部走 fake `session_manager`，**真 `StreamableHTTPSessionManager` + uvicorn 启动 + SIGTERM 关停从未端到端验过**
- 模块级单例直接重绑（`auth._store = X` 在 5+ 个文件，`audit._logger = None` 在 2 个文件），`importlib.reload(cfg)` 在 5 个文件——典型的"单测过、套件挂"漏洞源
- recorder 测试用真 `asyncio.sleep(0.02-0.2)` 等 supervisor tick，没装 `pytest-timeout`，CI loaded 时是首要 flake 源
- `tests/pentest.py` 和 `tests/loadtest/locustfile.py` 是孤立脚本，CI 从不跑
- `tests/live/` 全部 `-m 'not live'` skip，CI 中事实上死代码
- 缺 Hypothesis 类的属性测试（audit 解析、ticket TTL、路径校验都是天然候选）
- admin endpoint 审计无 e2e 验证；`dispatch_tool` 异常包装链没有真异常端到端测试

### 文档 (B)

**亮点**：
- README 是真正"操作员就绪"——`pipx install` → `mymcp install-service --yes` → systemctl start 一屏搞定
- 三种监控部署（Grafana Cloud / LGTM / pull-only）+ 完整 PromQL/SLO 配方都给齐了
- CLAUDE.md ~120 行就把请求流、关键文件、测试约定讲清楚，几乎不与 README 重复
- spec/plan 文档（`2026-06-04-recorder-resilience-design.md`）写得像 postmortem
- 每个工具有独立小节（参数表、返回、错误、JSON 示例），比 LLM 看到的 schema 更丰富

**主要病灶**：
- `.env.example` 用 `MCP_*` 旧前缀（见跨维度 #1）
- CHANGELOG 最后一条是 2026-05-15，PR #43/#45/#47/#48 都没进
- `tool_definitions.py` 里 LLM 真正看到的 schema 比 README 弱：`read_file` 描述仍写 "max 10000"（实际 50000），`bash_execute` 没说协议路径旁路、超时 = exit_code -1，`prepare_upload` 没说必须 curl 工作流
- schema 只有 1/9 工具设 `additionalProperties: false`，多数字段无 `default` / `enum`
- 安全说明被埋在 README 末尾，`bash_execute` 旁路只在两处括号里提了一句
- `docs/superpowers/plans/` 16 份 ~800KB 包含 4 月的执行脚本，跟"活跃 plan"无法区分；建议 `plans/done/` 归档
- 没有 CONTRIBUTING.md / "怎么加一个工具 / 加一个 metric" 的步骤化指南
- `PUBLISHING.md` 示例用 2.0.0（应同步到 2.1.1 或写 X.Y.Z）
- 无 troubleshooting / FAQ（recorder circuit open 怎么排查、disk full 行为等）

### 可观测性 (A−)

**亮点**：
- RED metrics 每工具齐全（`mymcp_tool_calls_total{tool,role,result}` + `mymcp_tool_duration_seconds`）+ 饱和度 gauge（`bash_inflight_processes` / `tokens_count{role}` / recorder 全套）
- recorder 7 个 metric 与 CLAUDE.md 文档**精确对齐**，`_record_outcome` 在每条代码路径都跑（success / no_events / bootstrap_required / llm_error / max_tokens / empty / unparseable / schema_invalid / apply_error）
- JSON log 把 `request_id`/`trace_id`/`span_id` 通过 filter 注入，audit log 也带相同字段，**Loki ↔ Tempo 关联开箱可用**
- audit schema 完整（含 sha256/head/tail 输出摘要），rotation 已配
- `recorder.supervisor.cycle` span 包住每个 tick，便于 trace 关联
- `/metrics` 端点用 `MYMCP_METRICS_TOKEN` 网关，纵深防御

**主要病灶**：
- `mymcp_http_requests_total{path,...}` 的 `path` 是原始 `scope["path"]`，当下没事，但**任何带动态段的新路由（如 `/files/raw/{ticket_id}`）会立刻 cardinality 爆炸**；dashboard 已经在 `by (method, path, status)` 查询
- `mymcp_audit_write_failures_total` 计数器**没有任何 alert 和 dashboard panel**——审计静默丢失是 SOC 红线
- `tokens_count` callback 把异常一律 swallow（`auth.py:175-176`），token store 损坏会静默
- transfer 上下行没有专门 counter，只通过通用 http counter 可见

### 安全 (C+)

**亮点**：
- token 熵足够（`token_hex(16)` = 128 bit，ticket `token_urlsafe(24)` ≈ 192 bit）
- token 文件 `0o600`
- `realpath` 防 symlink；protected-path 按 mode 分（recorder overview 可读不可写）
- ticket 单次消费 + atomic `tempfile + os.replace`，大小双重校验（Content-Length + streaming）
- bash subprocess `start_new_session=True` + SIGTERM→KILL grace + 自 PG 安全检查
- `Content-Disposition` 头注入已防

**主要病灶**：
1. **`mymcp.service.in` 完全没有 systemd 硬化指令**——上面 #4 提到的最高 ROI 修复
2. TokenStore 每次 validate 都全文重写、非原子（上面 #3）
3. `.env.example` 错前缀（上面 #1）
4. 工具输入 schema 几乎全部缺 `additionalProperties: false`，边界检查靠 Python 端 clamp
5. admin endpoint 无审计、无限流、token revoke 无审计追溯
6. CI 缺安全扫描：`pip-audit`、`bandit`、CodeQL 全无；Dependabot 只做版本升级
7. 下载路径 Content-Length 与 stream 之间存在 TOCTOU 风险（minor）

### 运维 (B)

**亮点**：
- CI 矩阵覆盖 Py 3.11/3.12/3.13
- mutation testing 在 CI 真跑（罕见）
- release workflow 含 wheel + 离线 bundle（含 ripgrep）+ PyPI trusted publisher + GitHub release notes
- recorder bootstrap retry + 断路器
- stateless transport 易横向扩

**主要病灶**：
- `/health` 永远返回 `{"status": "ok"}`，无 readiness 信号（token 存储已加载？recorder 健康？audit 目录可写？）
- Recorder 断路器跳闸后**只能重启服务恢复**（`task.py:88-92`），缺 admin reset 端点
- 无 SBOM、无 Dockerfile/OCI image，限制了编排选项
- 无 backup/DR 指南（tokens.json、audit log、recorder overview）
- 覆盖率/变异分数无门禁（同上）
- disk full 时 audit log fail-closed 但**无 operator alert**

---

## 优先级化的行动清单（按 ROI 排序）

### P0 — 今天就该改（每项 < 1 小时）
1. **重写 `.env.example`** 用 `MYMCP_*` 前缀，补 `MYMCP_RECORDER_LLM_MAX_TOKENS` / `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD`
2. CHANGELOG 补 PR #43/#45/#47/#48
3. CI 加 `--cov-fail-under=85` 和 `pip-audit` 步骤
4. README §"Recorder (optional)" 配置表补两个 recorder knobs

### P1 — 本周（每项 ½–1 天）
5. **systemd unit 加 `NoNewPrivileges=true`**（禁 setuid 提权，对产品功能无影响）。其他强隔离指令（`ProtectSystem=strict` / `ProtectHome=true` / `PrivateTmp=true` / `ReadWritePaths=...` / `CapabilityBoundingSet=` / `RestrictAddressFamilies=...`）**作为注释**写在 unit 文件里，附说明"启用后会限制 LLM 操作宿主机的能力，仅在高安全场景手动开启"。这些指令默认不启用——mymcp 的产品定位是让 LLM 全权操作 Linux，禁掉系统/家目录访问会破坏核心功能。
6. TokenStore `_save` 改原子写；`last_used` 改内存态 + 定期 flush
7. `MetricsMiddleware` 的 `path` label 用模板路径而非原始 path
8. `audit_write_failures` 加 dashboard panel + 在 README/CLAUDE.md 给出推荐 alert 查询（alert 规则由部署方自配，项目仅提供指标和参考查询，不 ship alert rule 文件）
9. `tool_definitions.py` 9 个工具描述对齐 README（修 read_file 上限、补 bash_execute 警告、补 transfer 工作流），全部加 `additionalProperties: false`
10. **Recorder 子系统重新设计**（详见下方 §"Recorder 子系统修订设计"）：
    - 断路器改为**事件驱动**：跳闸后停止定时 retry，新事件到达再尝试；5 次失败再次跳闸（取代当前"restart-only recovery"）
    - **Staleness 重新定义**：以 `pending_events > 0 && time_since_last_attempt > threshold` 为判据，而非"上次成功 merge 距今多久"
    - `overview.md` 内嵌 `_Last updated: ISO8601_` 行（重启后也可见）
    - banner 优先级与 Grafana SLO 改为复合判据
11. 装 `pytest-timeout` + `pytest-randomly` 暴露单例污染

### P2 — 本月（每项 2–3 天）
12. **配置重构**：去掉 `config.__getattr__` 历史属性 + 函数默认值里的 `config.X`；改成调用处 `s = get_settings()`
13. **结果协议重构**：`ToolResult` dataclass + 每工具 `audit_params()`，消灭 `call_tool` 的双形 switch
14. **Recorder 解耦（ToolRegistry 模式）**：引入中心 `ToolRegistry` + `ToolSpec(name, schema, permission, handler, mutates)`；核心工具在 `mymcp/tools/__init__.py` 自我注册，recorder 工具在 `mymcp/recorder/__init__.py`（仅当 `MYMCP_RECORDER_ENABLED=true` 才 import）自我注册。`mcp_server.py` 删掉 `_recorder_supervisor` 全局、`set/get_recorder_supervisor`、`server_overview` 硬编码分支、类型 cast；`READ_TOOLS`/`WRITE_TOOLS`/`MUTATING_TOOLS` 三个 set 从 ToolSpec 自动派生。`wiring.py` 持有 supervisor 实例直接调公开方法，**删掉私有属性 getattr 链**。recorder 禁用时 `server_overview` 在 `list_tools` 中根本不存在。和 #13 ToolResult 协议重构合并实施。
15. 文件 / token store I/O 通过 `anyio.to_thread.run_sync` 离开事件循环
16. 抽 `app_with_fake_session` 共享 fixture，加 1 个真服务端到端测试（`port=0` + 真 HTTP client + SIGTERM 验证清退）
17. **transfer 审计修正**：
    - ticket 持久化结构加 `issuer_token_id` / `issuer_role` 两个字段（mint 时写入、redeem 时读出回填审计）
    - `transfer/endpoints.py:74` 不再硬编码 `role="rw" if upload else "ro"`——redeemer 用 ticket bearer 凭证、根本没有 MCP role，写假 role 是审计欺骗
    - 审计 schema 区分 `tool="transfer_upload"` / `"transfer_download"`（不再用合成的 `transfer_redeem`），新增 `ticket_id` / `issuer_token_id` / `issuer_role` / `redeemer_ip` 字段
    - `"transfer_upload"` 加入 `MUTATING_TOOLS`（`recorder/events.py:29-37`），让 recorder 把"通过传输端点推上来的文件"也写进 changelog；当前所有 ticket 上传对 recorder 完全不可见

### P3 — 季度
18. 给 audit log 解析器、ticket TTL、`check_protected_path` 加 Hypothesis 属性测试
19. backup/DR runbook（tokens / audit / recorder 资产）
20. `docs/superpowers/plans/` 完成的归档进 `done/`
21. README 加一节 "Why we don't ship a Dockerfile"（解释容器化 mymcp 会丧失"操作宿主机"的核心价值；想用 Docker 的用户可以 10 行自己写 `FROM python:3.13-slim + pip install`）

---

## Recorder 子系统修订设计（P1 #10 展开）

### 现状的两个 bug

**Bug A — 断路器永久性**（`recorder/task.py:80-136`）：
- 跳闸后 supervisor 循环只是每 `interval` 醒一次检查 `_stop` 事件，**不再调 LLM**
- 没有恢复路径，需 `systemctl restart` 才能重置
- 影响：LLM 服务 5 分钟挂了 → 重启 mymcp 几天后才会无意中"修好" recorder

**Bug B — Staleness 误报**（`recorder/task.py:106-107` + `recorder/tool.py:58-65`）：
- `last_merge_ts` 只在**真正消费了事件**时更新；`no_events` 的空转 tick 故意不更新（原意是防止"事件处理器悄悄坏了"被掩盖）
- 结果：长时间没有写操作（系统空闲）→ `last_merge_ts` 旧 → banner 说"X 分钟过时"、Grafana 报警
- 实际是正常态被当成故障

### 重新设计：事件驱动 + 基于 backlog 的 staleness

**断路器与 supervisor 主循环改造**：

```
循环条件：
  - 阻塞等待 (新事件信号 ∨ stop 信号 ∨ interval 到期)
  - 醒来后：
      if stop: 退出
      if pending == 0: 重新阻塞（不调 LLM、不算失败）
      if pending > 0:
          tried = run_merge_cycle()
          if tried.success:
              consecutive_failures = 0
              circuit_open = False
              update last_merge_ts
          else:
              consecutive_failures += 1
              if consecutive_failures >= threshold:
                  circuit_open = True
                  # 不退出循环；仅停止"主动 retry"
          # 关键差异：circuit_open 状态下，仍然继续等新事件
          # 下次新事件触发时，再尝试一次 merge_cycle
          # 如果还失败：consecutive_failures 在 threshold 上继续累加
          # 如果成功：clear circuit_open + 清零
```

**新事件信号**：`EventTailer` 在检测到 audit.log 新增行时 `event_arrived.set()`，supervisor 在 `_stop.wait()` 的同时也 `await event_arrived.wait()`（用 `asyncio.wait(..., return_when=FIRST_COMPLETED)`）。

**Staleness 重新定义**：

| 指标 | 用途 |
|---|---|
| `pending_events` | **stale 主判据**：>0 才可能 stale |
| `last_merge_attempt_ts` | 新增字段；任何 tick 触发 merge 就更新（不论成败） |
| `last_merge_ts` | 仅成功更新，保留用于"信息展示"和"长期健康趋势" |
| `consecutive_failures` | 进 banner，让人知道为什么没赶上 |
| `circuit_open` | banner 优先级 1 |

**修订后的 banner 优先级**（`recorder/tool.py:_build_banner`）：

1. `circuit_open` → "_🛑 断路器已跳闸（连续 N 次失败），等待新事件触发下次尝试。最近错误：X_"
2. `pending_events > 0 && (now - last_merge_attempt_ts) > 2*interval` → "_⚠️ N 个事件待处理，已停滞 X 分钟_"（含 `last_error`）
3. `pending_events == 0` → 不显示 banner（正常态，overview 本身的 `_Last updated_` 行已经够了）
4. `pending_events > 0 && consecutive_failures > 0` → "_⚠️ 上次合并失败：X（将在下次事件到达时重试）_"

**overview.md 内嵌时间戳**：每次成功 merge 后，store 在 overview 顶部写入 `_Last updated: 2026-06-06T10:23:00Z_`（持久化在文件里，与运行时状态解耦）。重启后或离线查看时仍有意义。

### Grafana / SLO 调整

> 备注：项目仅 ship dashboard 和**推荐查询**（写在 CLAUDE.md 的 metric 表里），不 ship Prometheus alert rule 文件——alert 规则由部署方自配。

| 当前 panel / 推荐查询 | 问题 | 修订 |
|---|---|---|
| "Time Since Last Successful Merge" 单独 panel | 系统空闲时看起来像故障 | 改为 "Backlog & Last Attempt" 复合 panel：左轴 `pending_events`，右轴 `time() - last_merge_attempt_ts` |
| CLAUDE.md 推荐查询 `time() - last_success_ts > 3600` | 同上 | 改成 `pending_events > 0 AND (time() - last_merge_attempt_ts) > 1800` **OR** `circuit_open == 1` |
| `mymcp_recorder_merge_last_success_timestamp` gauge | 保留 | 仅作健康趋势图，不再作为推荐告警判据 |

新增 gauge：`mymcp_recorder_last_merge_attempt_timestamp`（每次尝试 merge 都更新，无论成败）。

### 测试要点
- 断路器跳闸后**再来新事件触发重试**这条路径（当前测试无法覆盖，因为 logic 不存在）
- `pending_events == 0` 时**不应**有任何 stale banner
- circuit_open 期间 5 次新事件全失败 → 状态稳定不抖动
- `EventTailer.event_arrived` 事件信号在 log rotation 后仍正确触发

### 工作量评估
约 1.5 - 2 天：~150 LOC 改动（`task.py`、`events.py`、`tool.py`、`overview.py`、3 个 grafana JSON、6-8 个新测试）+ 文档更新（CLAUDE.md 的 recorder metric 表 + README 的 SLO 配方）。

---

## 一句话总结

**这是一个"运维素养高出代码体量"的项目**——可观测性、CI、变异测试都是同等规模 OSS 罕见的水平；扣分点几乎都不是架构问题，而是"几个小时就能补上但持续放血"的细节（`.env.example` 错前缀、tokens.json 写放大、systemd 缺最基本的 `NoNewPrivileges`、CHANGELOG 落后、覆盖率无门禁），加上 recorder 子系统两处误报型 bug（断路器永久性、staleness 错把"空闲"当"故障"）。P0+P1 全部做完大概 3-4 天，整体能从 B 拉到 A−。
