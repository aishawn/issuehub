---
name: teamai-capture
description: Capture a concise team note, issue, or verified solution in TeamAI Issue Hub through MCP.
---

# TeamAI Capture

Use this skill only when the user explicitly asks to save, capture, record, search, append a solution, or resolve a TeamAI item.

## Capture workflow

1. Decide whether the content is a `note`, `issue`, or `solution`.
2. Remove API keys, tokens, passwords, cookies, and private credentials from the payload.
3. Search TeamAI for a likely duplicate when the content describes a problem.
4. Show a short preview containing title, type, tags, and the redacted body.
5. After confirmation, call `teamai_create_note` or `teamai_create_issue`.
6. Return the local record id and GitLab URL when available.

## Problem format

Use this structure for issues:

```markdown
## 问题现象
## 环境
## 复现步骤
## 预期结果
## 实际结果
## 已尝试方案
## 根因
## 解决方案
## 验证结果
```

Use `teamai_append_solution` for an intermediate finding and `teamai_resolve` only after the solution has been verified.
