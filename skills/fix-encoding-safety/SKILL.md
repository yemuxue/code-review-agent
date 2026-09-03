---
name: fix-encoding-safety
description: 修复写文件时必须保持原文件编码与行尾风格，禁止引入 BOM/乱码/行尾混用
roles: [fixer]
triggers: [encoding, utf-8, crlf, bom, mojibake, 乱码, 编码, 行尾, 中文]
---

- write_file 输出必须与原文件编码一致，项目默认 UTF-8，读取/写入带 encoding="utf-8" (write_file 不改变原文件编码；读写均显式 UTF-8)。
- 统一换行符 \n；不得在同一文件内混用 CRLF/LF (保持单一换行风格，禁止 CRLF/LF 混用)。
- 不得引入 BOM；修复内容中的中文必须完整保留，禁止任何乱码/mojibake (禁止引入 BOM 与中文乱码)。
- 若原文件本就是 CRLF，保持 CRLF 一致，并在修复报告中注明该文件的行尾风格 (原文件含 CRLF 时保持一致并在报告中注明)。
- 修复后重读一遍写入结果，确认编码与中文内容未被破坏 (写入后自查一遍)。
