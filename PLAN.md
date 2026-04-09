# NoteWeaver 修复计划

基于代码审计发现的 5 个问题，按优先级排列，附带具体修复方案、影响范围和测试策略。

---

## P0: `import_directory()` 中 `type: synthesis` 文件路由到错误目录

**问题**

`vault.py:742` 把 `hub`、`canonical`、`note`、`synthesis` 四种类型的文件全部路由到 `wiki/concepts/`，而 vault 初始化时明确创建了 `wiki/synthesis/` 目录。这违反了项目自身的目录结构设计。

**位置** `src/noteweaver/vault.py` — `import_directory()` 方法（第 742 行）

```python
# 当前代码
if fm and fm.get("type") in ("hub", "canonical", "note", "synthesis"):
    dest = f"wiki/concepts/{rel_name}"
```

**修复方案**

按 `type` 分流到正确目录：

```python
page_type = fm.get("type") if fm else None
if page_type == "synthesis":
    dest = f"wiki/synthesis/{rel_name}"
elif page_type == "journal":
    dest = f"wiki/journals/{rel_name}"
elif page_type in ("hub", "canonical", "note"):
    dest = f"wiki/concepts/{rel_name}"
else:
    # 无 frontmatter 或未知类型：包装为 note
    title = f.stem.replace("-", " ").replace("_", " ").title()
    header = (
        f"---\ntitle: {title}\ntype: note\n"
        f"summary: Imported from {f.name}\n"
        f"tags: [imported]\ncreated: {today}\nupdated: {today}\n---\n\n"
    )
    content = header + content
    dest = f"wiki/concepts/{rel_name}"
```

**影响范围** 仅 `vault.py` 一个方法，不影响其他写入路径（`write_page`、`promote_insight` 等由 LLM 自己决定路径）。

**测试**
- 新增 `test_import_synthesis_to_correct_dir`: 导入一个 `type: synthesis` 的 md 文件，断言落入 `wiki/synthesis/` 而非 `wiki/concepts/`
- 现有 `test_vault.py` 中的 import 测试需确认不回归

---

## P1: `updated` 时间戳未被自动维护，导致 Recent 列表失真

**问题**

以下写入操作修改了页面内容但不更新 frontmatter 中的 `updated` 字段：
1. `append_section` (`definitions.py:656`)
2. `append_to_section` (`definitions.py:680`)
3. `promote_insight` 追加到已有页 (`definitions.py:911`)
4. `add_related_link` (`definitions.py:751`)
5. `_save_session_journal` 追加到已有 journal (`cli.py:301`)

而 `rebuild_index()` (`vault.py:611`) 按 `updated` 排序生成 Recent 列表，导致频繁编辑的页面可能因初始 `updated` 很旧而排名靠后。

**位置** 多个文件

**修复方案**

在 `vault.py` 的 `write_file()` 方法中增加自动更新逻辑。这是最集中的修复点，因为所有写入最终都经过 `write_file()`。

```python
def write_file(self, rel_path: str, content: str) -> None:
    """Write a file in the wiki area."""
    path = self._resolve(rel_path)
    if self._is_in_sources(path):
        raise PermissionError(...)
    if not rel_path.startswith("wiki/") and not rel_path.startswith(".schema/"):
        raise PermissionError(...)

    # 自动更新 updated 时间戳
    if rel_path.startswith("wiki/") and rel_path not in ("wiki/index.md", "wiki/log.md"):
        content = self._touch_updated(content)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    self._index_file(rel_path, content)
    self.backlinks.update_page(rel_path, content)
    ...
```

`_touch_updated()` 的实现：

```python
def _touch_updated(self, content: str) -> str:
    """Update the 'updated' field in frontmatter to today's date."""
    from noteweaver.frontmatter import extract_frontmatter, FRONTMATTER_PATTERN
    fm = extract_frontmatter(content)
    if fm is None or "updated" not in fm:
        return content  # 无 frontmatter 或无 updated 字段则不干预
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if str(fm.get("updated", "")) == today:
        return content  # 已经是今天，跳过
    fm["updated"] = today
    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
    body = FRONTMATTER_PATTERN.sub("", content, count=1)
    return f"---\n{fm_str}\n---\n{body}"
```

**设计考量**
- 只在已有 `updated` 字段时更新（不对无 frontmatter 的文件注入）
- 放在 `write_file` 层而非每个 handler 中，避免遗漏且减少代码重复
- `wiki/index.md` 和 `wiki/log.md` 是结构文件，不需要 `updated` 语义，跳过

**影响范围** `vault.py` 的 `write_file()` 是所有写入的瓶颈点。需要确保：
- 新建页面时 `updated` 是创建日期（handler 自己设的）不会被二次覆盖（不会，因为值相同时跳过）
- `write_page` 显式设置 `updated` 的情况不受影响（handler 写的值当天写入时不变）

**测试**
- `test_append_section_updates_timestamp`: append_section 后读取 frontmatter，断言 `updated` 为今天
- `test_promote_to_existing_updates_timestamp`: 同上
- `test_write_file_skips_update_when_no_frontmatter`: 确认对无 frontmatter 的文件不做修改
- `test_write_file_skips_index_and_log`: 确认 `wiki/index.md` 不受影响

---

## P2: `promote_insight` 无法创建 `canonical` 或 `synthesis` 类型的页面

**问题**

`digest` 提案允许目标类型为 `note/canonical/synthesis`（`cli.py:445`），但 `promote_insight` 创建新页时硬编码 `type: note`（`definitions.py:941`），路径固定为 `wiki/concepts/`。无法真正产出 canonical 或 synthesis。

**位置** `src/noteweaver/tools/definitions.py` — `handle_promote_insight()` 函数

**修复方案**

为 `promote_insight` 增加可选参数 `target_type`，默认为 `"note"`：

1. **工具 schema 更新** — 在 `TOOL_SCHEMAS` 中给 `promote_insight` 增加 `target_type` 参数：

```json
{
    "name": "target_type",
    "type": "string",
    "enum": ["note", "canonical", "synthesis"],
    "description": "Target page type. Defaults to 'note'.",
    "required": false
}
```

2. **Handler 逻辑更新**：

```python
def handle_promote_insight(
    vault, title, content, source_journal="", tags=None, target_type="note",
):
    ...
    # 根据 target_type 决定路径和 frontmatter
    if target_type == "synthesis":
        path = f"wiki/synthesis/{slug}.md"
    else:
        path = f"wiki/concepts/{slug}.md"

    # canonical 需要 sources 字段
    sources_line = ""
    if target_type == "canonical" and source_journal:
        sources_line = f"sources: [{source_journal}]\n"

    fm = (
        f"---\ntitle: {title}\ntype: {target_type}\n"
        f"summary: Insight promoted from journal\n"
        f"{sources_line}"
        f"tags: [{tag_str}]\n"
        f"created: {today}\nupdated: {today}\n---\n\n"
    )
    ...
```

3. **Policy 层不需要改动** — `promote_insight` 在 attended 模式下已经允许 CONTENT 写入，`frontmatter.py` 的 `validate_frontmatter` 会自动校验 canonical 的 `sources` 字段。

**设计考量**
- 向后兼容：`target_type` 默认 `"note"`，现有行为不变
- synthesis 类型走 `write_page` 的 policy check（≥2 个 wiki-links）更严格，但 `promote_insight` 的场景通常是从 journal 提取，未必有 wiki-links。方案一：在 `promote_insight` 中不走 synthesis link 检查（因为 promote 本质是种子页，后续再丰富）；方案二：保持 policy 检查，由 LLM 自己确保内容质量
- 建议采用方案一，因为 promote 是"初始提升"，synthesis 的 link 要求可以在后续编辑时满足

**影响范围** `definitions.py`（handler + schema）

**测试**
- `test_promote_creates_synthesis_in_correct_dir`: `target_type="synthesis"` 时文件落入 `wiki/synthesis/`
- `test_promote_creates_canonical_with_sources`: `target_type="canonical"` 时 frontmatter 包含 `sources`
- `test_promote_default_type_unchanged`: 不传 `target_type` 时行为与当前一致
- 现有 4 个 promote 测试不应回归

---

## P3: Gateway cron lint 未设为 unattended 模式

**问题**

`gateway.py:177-188` 中 lint cron 直接调用 `agent.chat()` 但不像 digest cron 那样设置 `set_attended(False)`。这意味着自动 lint 在 attended 模式下运行，理论上可以执行 CONTENT 写入，与"无人值守只写 proposal"的原则不一致。

（另一个 agent 没有提到这个问题，但它比 `add_related_link` 的口子更大。）

**位置** `src/noteweaver/gateway.py` — `_run_cron()` 方法（第 176-188 行）

**修复方案**

与 digest cron 保持一致，给 lint cron 加上 unattended 保护：

```python
# --- Lint ---
if now - last_lint >= lint_interval:
    log.info("Cron: running lint...")
    async with self._lock:
        self.agent.set_attended(False)
        try:
            for chunk in self.agent.chat(
                "Quick health check: use vault_stats and report any issues. Be brief."
            ):
                if not chunk.startswith("  ↳"):
                    log.info("Lint result: %s", chunk[:200])
        except Exception as e:
            log.error("Cron lint failed: %s", e)
        finally:
            self.agent.set_attended(True)
    last_lint = now
```

**影响范围** 仅 `gateway.py` 一处，改动极小。

**测试** gateway 目前没有直接的单元测试（依赖真实 asyncio + adapter），可以考虑：
- 对 `_run_cron` 的 attended 状态做 mock 测试
- 或仅做代码审查确认（风险很低）

---

## P4: `add_related_link` 在 unattended 模式下可修改概念页

**问题**

`add_related_link` 被归类为 `_STRUCTURE_TOOLS`（`policy.py:44`），所以 unattended 模式下允许执行。但它实际修改的是概念页/synthesis 页正文中的 `## Related` 区块。

**位置** `src/noteweaver/tools/policy.py`（第 44 行、第 68 行）

**评估：不建议修复，保持现状。**

理由：
1. 这是**有意的设计选择**，有专门的测试 `test_unattended_allows_related_link` 确认此行为
2. Related 链接是**结构性元数据**（页面间拓扑关系），不是知识内容本身
3. 操作是**幂等的**（已存在的链接会跳过），风险极低
4. 如果 digest cron 发现页面之间的新关联，能自动维护链接是有价值的

如果未来确实需要收紧，建议的路径是：将 Related 链接从页面正文分离到独立 metadata 文件（但这是架构级改动，不在本次范围内）。

**动作** 无代码改动。在 `policy.py` 的模块文档注释中补充说明这一设计决策，使意图更显式。

---

## P5: `ingest` 编排完全依赖 LLM，缺乏强制执行

**问题**

`nw ingest <url>` 的标准流程（先存 source → 再建 synthesis → 再建 hub → 再更 index/log）只存在于 prompt 中，不保证每次都按序执行。

**评估：不建议改为硬编码 pipeline，但可增加轻量级 post-condition check。**

理由：
1. LLM-driven orchestration 是项目的核心设计哲学
2. 加硬编码 pipeline 会大幅增加代码复杂度，且丧失灵活性
3. 已有的防护（sources/ create-only、frontmatter 校验、dedup 检查）覆盖了关键风险

**可选改进**（低优先级，可以留到后续迭代）：

在 `cli.py` 的 `cmd_ingest` 中，session 结束后增加一个轻量级检查：

```python
# 在 _finalize_session 后
warnings = []
if not any(vault.list_files("sources")):
    warnings.append("⚠ No source file was saved during ingest")
# 其他可选检查...
if warnings:
    console.print("\n".join(warnings))
```

**动作** 本次不改动。记录为 TODO，后续有需求时再实施。

---

## 执行顺序

| 顺序 | 问题 | 涉及文件 | 改动量 |
|------|------|----------|--------|
| 1 | P0: import synthesis 目录 | `vault.py` | ~5 行 |
| 2 | P1: updated 自动维护 | `vault.py` | ~25 行 |
| 3 | P2: promote_insight 类型 | `definitions.py` | ~30 行 |
| 4 | P3: lint cron unattended | `gateway.py` | ~5 行 |
| 5 | P4: add_related_link 文档 | `policy.py` | ~3 行注释 |

每个修复独立提交，附带对应测试。全部修复后运行完整测试套件确认无回归。
