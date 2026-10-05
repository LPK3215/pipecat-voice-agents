# 语音交互体验（VOICE-UX）

> **本文记录**：**"点麦克风 → 说话 → 自动发送 → 自动朗读"** 这套体验**具体怎么做的、怎么复现**。
>
> 与 [`INTEGRATION.md`](INTEGRATION.md) 配套：**那篇讲"语音怎么接进来"（通道），这篇讲"交互默认值"（体验）。**
> 基线版本：Open WebUI `v0.11.4`（行号以此为准，换版本需重新定位）。

---

## 0. 一句话

> **Open WebUI 原生就有这套交互的全部零件** —— 我们要做的只是**把 3 个默认值打开** + **修掉 1 处遮挡**，**没有新增任何逻辑**。

---

## 1. 改动清单（3 处，可精确复现）

| # | 想达到的效果 | 文件 | 位置 | 原来是 | 改成 |
|---|---|---|---|---|---|
| 1 | 语音转写后**自动发送** | `src/lib/components/chat/MessageInput.svelte` | 第 **1754** 行（`onConfirm` 里） | `if ($settings?.speechAutoSend ?? false)` | `?? true` |
| 1' | 同上（设置页读到的默认值） | `src/lib/components/chat/Settings/Audio.svelte` | 第 **94** 行 | `speechAutoSend = $settings.speechAutoSend ?? false` | `?? true` |
| 2 | 回复**自动朗读** | `src/lib/components/chat/Settings/Audio.svelte` | 第 **95** 行 | `responseAutoPlayback = $settings.responseAutoPlayback ?? false` | `?? true` |
| 3 | 录音时**不遮挡输入框** | `src/lib/components/chat/MessageInput.svelte` | 第 **1761** 行（`<form>` 的 class） | `class="w-full flex flex-col gap-1.5 {recording ? 'hidden' : ''}"` | `class="w-full flex flex-col gap-1.5"` |
| **4** | **停顿 N 秒自动结束录音** | `src/lib/components/chat/MessageInput/VoiceRecording.svelte` | 第 **154–162** 行（`detectSound` 内） | **整段被上游注释掉**（所以必须手动点 ✓） | **取消注释 + 恢复**（见下方原文） |

### 第 4 处：上游注释掉的「静音自动确认」

这是**最关键的一处** —— 不改它，前面几处都白搭（因为录音根本不会自动结束）。

上游的原始代码（**被注释掉**）：

```js
// if (domainData.some((value) => value > 0)) {
// 	lastSoundTime = Date.now();
// }

// if (recording && Date.now() - lastSoundTime > 3000) {
// 	if ($settings?.speechAutoSend ?? false) {
// 		confirmRecording();
// 	}
// }
```

本分支恢复为（标注了 `[pipecat-open-webui 本分支改动]`）：

```js
// [pipecat-open-webui 本分支改动] 恢复「静音自动确认」：停顿 3s 且开启自动发送时，自动结束录音并提交
if (domainData.some((value) => value > 0)) {
	lastSoundTime = Date.now();
}

if (recording && Date.now() - lastSoundTime > 3000) {
	if ($settings?.speechAutoSend ?? true) {
		confirmRecording();
	}
}
```

**机制**：`detectSound()` 每帧读取频谱数据；**只要有声音就刷新 `lastSoundTime`**；**超过 3 秒没声音** → 调 `confirmRecording()` → 停录 → 转写 → `onConfirm` → 自动发送。

> 两处上游来源对比，说明"为什么必须手动点 ✓"：
> - **`stt.engine = 'web'`（浏览器识别）路径**：`speechRecognition.onend` 里**本来就会** `confirmRecording()` → 自动
> - **`stt.engine = ''`（本地 Whisper）路径**（我们在用）：靠上面这段被注释的逻辑 → **不恢复就得手动点**

> 第 1 处决定"**发不发**"，第 3 处决定"**看不见文字**"—— 这两处合起来才让你"看到转写文字后自动发送"。

---

## 2. 不用改的（本来就内置）

| 能力 | 位置 | 说明 |
|---|---|---|
| **声波可视化**（音量高低） | `MessageInput/VoiceRecording.svelte` | 用 Web Audio `AnalyserNode` 实时算音量，画成波纹 |
| **停顿自动停录** | 同上 | 静音检测（VAD）到阈值就自动停 |
| **生成期间禁止语音输入** | `MessageInput.svelte:2629` | 录音按钮**只在消息完成时显示**：`{#if ... history.messages[history.currentId]?.done == true}` |
| **点击打断** | `CallOverlay.svelte` | 通话模式的 "tap to interrupt" |
| **转写 → 插入输入框** | `MessageInput.svelte:1750` | `await insertTextAtCursor(text)` |

---

## 3. 改完之后的完整链路

```
点麦克风
  → 说话                     ← 声波实时显示音量（内置）
  → 停顿到阈值 → 自动停录     ← VAD 静音检测（内置）
  → 转写 → 文字插入输入框      ← 输入框不再被隐藏，你能看到（改动 3）
  → speechAutoSend=true     → 自动发送（改动 1）
  → 模型回答（可调工具/记忆）   ← 官方链路，未改
  → responseAutoPlayback=true → 自动朗读（改动 2）
  → 等下一句                 ← 生成期间麦克风按钮自动隐藏（内置）
```

---

## 4. 从零复现（换一个 Open WebUI 版本时）

```bash
# 1. 按第 1 节表格改那 3 处（用 grep 重新定位行号，不要硬套行号）
grep -n "speechAutoSend ?? false"  src/lib/components/chat/MessageInput.svelte
grep -n "responseAutoPlayback ?? false" src/lib/components/chat/Settings/Audio.svelte
grep -n "recording ? 'hidden' : ''" src/lib/components/chat/MessageInput.svelte

# 2. 重新构建前端（必须调大 Node 堆内存，否则 OOM）
NODE_OPTIONS="--max-old-space-size=10240" npm run build

# 3. 重启后端
cd backend && PORT=8000 PATH="$PWD/.venv/bin:$PATH" ./start.sh

# 4. 浏览器强制刷新（Ctrl+Shift+R），否则还是旧页面
```

> ⚠️ **务必分清「默认值」与「已保存设置」**：`?? true` **只对没设置过的用户生效**。
> 如果设置里已存过 `false`（例如你以前手动关过），**以保存值为准** —— 去 `设置 → 音频` 确认那两个开关是开的。

---

## 5. 界面上对应的设置项（不想改代码也能开）

| 设置项（中文） | 位置 | 变量名 |
|---|---|---|
| **语音转录文字后即时自动发送** | 设置 → 个人 → 音频 → **语音识别区** | `speechAutoSend` |
| **自动播放回复** | 设置 → 个人 → 音频 → **语音合成区** | `responseAutoPlayback` |

> 这两个设置项**默认是关的**。改代码 = 把默认打开；不改代码 = 手动在界面打开（效果一样）。

---

## 6. 结论（复现时记住这一条）

> **这套"像打电话"的体验 = 1 条内置链路 + 3 个默认值改动 + 0 行新增逻辑。**

- **通道**（能听见/能说）→ 见 [`INTEGRATION.md`](INTEGRATION.md)
- **形态**（A/B/C 三档）→ 见 [`VOICE-MODES.md`](VOICE-MODES.md)
- **体验**（自动发送/自动朗读/不遮挡）→ **就是本文**
