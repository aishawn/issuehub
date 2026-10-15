---
name: teamai-capture
description: Search and capture team issues and notes in TeamAI Issue Hub through MCP. Verified resolves auto-publish team knowledge.
---

# TeamAI Capture

Use this skill when the user asks to save, capture, record, search, append a solution, resolve an issue, or promote a verified result into shared team knowledge.

## Capture workflow

1. Search TeamAI for existing team knowledge first (`teamai_search` with `kind: "knowledge"`), then search related issues and notes if needed. Page through results with `limit` and `offset` when a result page is full. Reuse a relevant result instead of creating a duplicate.
2. For a quick `note` (随手记), identify the username used by Issue Hub. If the user already supplied it in this conversation, reuse it; otherwise ask before the first note and retain it for later notes in the same conversation. Do not infer a username from an email, machine username, Git identity, or unrelated profile. The Web UI also remembers the entered username in that browser and allows switching it.
3. Every new note must have an **所属项目**, an **所属工作类型**, and a visibility choice. Ask for missing values before creating the record. Reuse project/type values stated for this note; never guess. Offer `算法调研`, `算法预研`, `模型部署`, `组件开发`, `项目支撑`, `工作日常`; use one of these supported types. If project is unknown, ask the user to provide it. Ask whether the note should be `shared` (team shared) or `private` (personal view); shared is the default if the user has no preference, but make the choice visible in the capture preview.
4. Classify the new information as a quick `note`, an unresolved `issue`, or a verified experience. An unverified suggestion is never a solution or reusable knowledge.
5. Capture technology stack when known. For issues, include environment, reproduction steps, expected/actual result, attempted fixes, root cause, and verification evidence where available. Leave unknown facts blank; never invent them.
6. Remove API keys, tokens, passwords, cookies, and private credentials from the payload.
7. Personal view is weak username-based separation, not authentication. Anyone can claim another username. Tell users not to store sensitive/private material there. Personal notes are excluded from GitLab sync and team knowledge review. Only explicitly shared notes can enter team-wide search.
8. When the user explicitly asks to save or capture something, create the note/issue after the search and required note fields are available, without asking them to approve the same capture again. If their intent to save is unclear, show a concise preview and ask first. Never publish a reusable knowledge article without the separate explicit approval described below.
9. Return the TeamAI record id, visibility, and GitLab URL when available.

## Note identity and classification

- Pass the user-selected username as the capture context (`username` / `X-TeamAI-User`) and as the owner of the note. This username is not verified identity and must never be presented as secure authentication.
- Include `所属项目：<project>` and `所属工作类型：<one selected value>` in note metadata/body when using a client that cannot persist those as first-class fields. When the API exposes `project` and `work_type`, populate those fields instead of relying only on body text.
- Ask one concise bundled follow-up for the account (first note only), project, and work type when several are missing. A user can answer with the project and type while omitting account only if the account was already established or they explicitly decline it.

## Attachments

- If the user provides a PPT/PPTX, HTML, Markdown, PDF, Office document, text file, or image that belongs with a note or issue, create/search for the record first, then upload the file through the TeamAI Web detail page or `POST /v1/records/{id}/attachments` using multipart form field `file`.
- The API returns attachment metadata; list attachments by calling `teamai_get_issue` (the response includes `attachments`) or `teamai_list_attachments`. Downloads use `/v1/records/{id}/attachments/{attachment_id}` with the matching `X-TeamAI-User` context for personal notes. Username context is not identity verification.
- Mention each attached filename in the record body when useful. Never upload credentials, secrets, or files the user did not ask to preserve.
- Current upload limit is 20 MiB per file, 30 files per record. Supported extensions are PDF, PPT/PPTX, DOC/DOCX, XLS/XLSX, MD, TXT, HTML, and PNG/JPG/GIF/WebP.

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

Use `teamai_append_solution` (with `username`) for an intermediate finding. A verified experience should be attached to its source issue; use `teamai_resolve` only after the reporter says the fix was verified, and include how it was verified. If there is no existing issue, create one with the known context, then resolve it with the verification evidence. **Resolving auto-publishes team knowledge** from the issue content—no separate approval step.

## Knowledge publishing

- Before resolving, obtain the resolver's display name if needed. Pass it as `username` to `teamai_resolve`. The name is self-reported and recorded in activity history, not authenticated.
- After resolve, search `kind: "knowledge"` to confirm the article. Reopening hides knowledge until the issue is resolved again (auto-republish).
- `teamai_knowledge_candidates` / `teamai_publish_knowledge` are **admin-only** leftovers for rare manual curation. Do not use them in the normal resolve flow.
- Notes: `teamai_update_note` / `teamai_delete_note` require the owner username. Issues: `teamai_reopen_issue` / `teamai_delete_issue` require username.
