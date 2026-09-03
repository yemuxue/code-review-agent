---
name: security-review-rules
description: 安全审查规则：硬编码密钥/SQL 注入/危险动态执行/路径穿越必须按 SECURITY 报出并给出可落地修复
roles: [executor, reviewer, fixer]
triggers: [security, secret, credential, token, hardcoded, injection, sql, eval, xss, csrf, 安全, 注入]
---

- Hardcoded keys/tokens/credentials → report SECURITY, suggest env var or secret store (硬编码密钥/凭据 → 建议改环境变量或密钥库)。
- String-built SQL → parameterized queries / ORM only (字符串拼接 SQL → 一律参数化查询)。
- eval/exec/compile/shell=True on unsanitized input → allowlist or ast.literal_eval (不可信输入上的动态执行 → 白名单或 ast.literal_eval)。
- Path built from user input without containment → resolve() + relative_to() 防目录穿越。
- Fix suggestions must be concrete and local (修复建议必须可落地、局部化，不扩大改动面)。
- Do NOT invent CVEs or version claims you cannot verify (无法核实的 CVE/版本声明不要编造)。
