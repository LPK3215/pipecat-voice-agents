# 许可证补充说明（pipecat-open-webui）

本仓库是 **[Open WebUI](https://github.com/open-webui/open-webui)** 的二次开发分支（衍生作品）。
仓库内**不同部分适用不同许可证**，特此说明——这是合法且常见的「双协议」结构。

---

## 1. 上游代码 → 沿用 Open WebUI License

除第 2 节列出的「本分支新增内容」外，本仓库**其余所有文件**均为上游 Open WebUI 的代码，
**沿用上游许可**：

- [`LICENSE`](./LICENSE) —— 当前适用（Open WebUI License，含品牌保留条款）
- [`LICENSE_NOTICE`](./LICENSE_NOTICE) / [`LICENSE_HISTORY`](./LICENSE_HISTORY) —— 历史许可记录
- [`CONTRIBUTOR_LICENSE_AGREEMENT`](./CONTRIBUTOR_LICENSE_AGREEMENT)

> **本分支不改变、不移除上游的任何许可条款，也不移除 "Open WebUI" 品牌。**

---

## 2. 本分支新增内容 → MIT License

以下为**本分支作者原创、独立创建**的文件（**不与上游代码混合**），
其著作权归本分支作者（见 [`AUTHORS`](./AUTHORS)），采用 **MIT License**：

- `voice-docs/`（全部）
- `docs/`（本分支生成的可视化产物）
- `scripts/visualization/`（本分支新增的脚本）
- `CONTRIBUTING.md`、`FAQ.md`、`AUTHORS`、`LICENSE-SUPPLEMENT.md`
- `README.md` 顶部的「本分支说明」段落

```
MIT License

Copyright (c) 2026 cnb.lpk

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 3. 再分发时的注意事项

如果你要**再分发**本仓库（或它的衍生作品），必须：

1. 同时遵守 **Open WebUI License**（第 1 节）与 **MIT License**（第 2 节）
2. **保留** `LICENSE` / `LICENSE_NOTICE` / `LICENSE_HISTORY` 与全部版权声明
3. **保留**界面上的 "Open WebUI" 品牌，除非满足 Open WebUI License 第 4 条的任一豁免条件：
   - ① 30 天滚动周期内终端用户 ≤ 50 人；② 获得版权方书面许可；③ 持有企业许可

---

## 4. 一句话总结

> **上游的归还上游（Open WebUI License），你自己的归你自己（MIT）。**
> 不违法、不越界，也让这个分支真正成为「你的项目」。
