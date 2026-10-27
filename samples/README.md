# 放测试 PDF 到这里

- 支持 .pdf；1–2 份即可（一份扫描件、一份双栏）。
- 文件名用 ASCII：scan1.pdf / twocol1.pdf。
- 不要放身份证/手机号/真实姓名等敏感信息；内部文件请先脱敏。
- 放好后告诉 agent 文件名 + 想验证的 3–5 个关键字段/问题。

## 已验证（2026-10-27）

| 文件 | 结果 |
|---|---|
| `sisu-handbook-2025.pdf`（文字版） | ✅ pypdf 抽出 **340,277** 字；关键词命中「上海外国语大学 / 学生手册」 |
| `sisu-handbook-scan.pdf`（扫描版） | ✅ **明确报错**（`ParseError`：没有可提取文本，建议 OCR/版面解析）——不是静默空串 |

**已知限制**：pypdf 不识别双栏/表格，页眉水印与正文会交错；要干净版面用 `PDF_BACKEND=docling|mineru`（未装）。
验收脚本：`scripts/check_pdf_samples.py`；判据：`tests/test_pdf_samples.py`。
