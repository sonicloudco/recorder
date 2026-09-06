(() => {
  "use strict";

  const UUIDS = {
    service: "0000ae20-0000-1000-8000-00805f9b34fb",
    write: "0000ae21-0000-1000-8000-00805f9b34fb",
    notify: "0000ae22-0000-1000-8000-00805f9b34fb",
    keyNotify: "0000ae23-0000-1000-8000-00805f9b34fb",
  };

  const els = {};
  const state = {
    device: null,
    server: null,
    writeChar: null,
    notifyChar: null,
    keyNotifyChar: null,
    seq: 0,
    connected: false,
    files: [],
    listIdleTimer: null,
    logs: [],
    download: null,
    realtime: {
      name: "",
      chunks: [],
      bytes: 0,
      active: false,
    },
  };

  class FrameParser {
    constructor(source, onFrame) {
      this.source = source;
      this.onFrame = onFrame;
      this.buf = new Uint8Array(0);
    }

    push(bytes) {
      this.buf = concatBytes([this.buf, bytes]);
      const frames = [];

      while (this.buf.length >= 6) {
        const magicIndex = this.buf.indexOf(0x5a);
        if (magicIndex < 0) {
          log("WARN", `${this.source} 丢弃无帧头数据 ${hex(this.buf)}`);
          this.buf = new Uint8Array(0);
          break;
        }
        if (magicIndex > 0) {
          log("WARN", `${this.source} 丢弃帧头前噪声 ${hex(this.buf.slice(0, magicIndex))}`);
          this.buf = this.buf.slice(magicIndex);
        }
        if (this.buf.length < 6) break;

        const len = readU16LE(this.buf, 4);
        const total = 6 + len;
        if (len > 8192) {
          log("ERR", `${this.source} LEN 异常 ${len}，跳过当前帧头`);
          this.buf = this.buf.slice(1);
          continue;
        }
        if (this.buf.length < total) break;

        const frame = this.buf.slice(0, total);
        this.buf = this.buf.slice(total);
        const seq = frame[1];
        const expected = readU16LE(frame, 2);
        const actual = crc16Xmodem(frame.slice(4));
        if (expected !== actual) {
          log("ERR", `${this.source} CRC 错误 seq=${seq} expect=0x${toHex16(expected)} actual=0x${toHex16(actual)} raw=${hex(frame)}`);
          continue;
        }
        const data = frame.slice(6);
        frames.push({ source: this.source, seq, data, raw: frame });
      }

      for (const frame of frames) this.onFrame(frame);
    }
  }

  const parsers = {
    ae22: new FrameParser("AE22", handleFrame),
    ae23: new FrameParser("AE23", handleFrame),
  };

  document.addEventListener("DOMContentLoaded", () => {
    bindElements();
    bindEvents();
    updateSecureState();
    setConnected(false);
    log("INFO", "页面已加载。Web Bluetooth 需要 Chrome/Edge 与 localhost 或 HTTPS 环境。");
  });

  function bindElements() {
    for (const id of [
      "secureState", "bleState", "connectBtn", "compatConnectBtn", "disconnectBtn", "safeSmokeBtn",
      "batteryText", "capacityText", "firmwareText", "recordStateText", "recordTimeText", "gainText",
      "downloadName", "downloadOffset", "downloadBtn", "segDownloadBtn", "segStart", "segEnd",
      "downloadStatus", "fileRows", "abortImportBtn", "deleteAllBtn", "allowDelete",
      "rtName", "rtBytes", "saveRealtimeBtn", "gainSelect", "setGainBtn",
      "rawType", "rawCmd", "rawParams", "sendRawCmdBtn", "rawFrame", "sendRawFrameBtn",
      "selfTestBtn", "selfTestOutput", "clearLogBtn", "exportLogBtn", "log",
    ]) {
      els[id] = document.getElementById(id);
    }
  }

  function bindEvents() {
    els.connectBtn.addEventListener("click", () => connect(false));
    els.compatConnectBtn.addEventListener("click", () => connect(true));
    els.disconnectBtn.addEventListener("click", disconnect);
    els.safeSmokeBtn.addEventListener("click", runSafeSmoke);
    els.downloadBtn.addEventListener("click", () => requestDownloadFromInput());
    els.segDownloadBtn.addEventListener("click", requestSegmentDownload);
    els.abortImportBtn.addEventListener("click", () => sendCommand(2, 7));
    els.deleteAllBtn.addEventListener("click", deleteAllFiles);
    els.setGainBtn.addEventListener("click", () => sendCommand(3, 27, [Number(els.gainSelect.value)]));
    els.saveRealtimeBtn.addEventListener("click", saveRealtimeAudio);
    els.sendRawCmdBtn.addEventListener("click", sendRawCommand);
    els.sendRawFrameBtn.addEventListener("click", sendRawFrame);
    els.selfTestBtn.addEventListener("click", runSelfTest);
    els.clearLogBtn.addEventListener("click", clearLog);
    els.exportLogBtn.addEventListener("click", exportLog);

    document.querySelectorAll("[data-cmd]").forEach((btn) => {
      btn.addEventListener("click", () => runNamedCommand(btn.dataset.cmd));
    });

    els.fileRows.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const index = Number(button.dataset.index);
      const file = state.files[index];
      if (!file) return;
      const action = button.dataset.action;
      if (action === "wav") requestDownload(fileDownloadName(file, "wav"), 0, fallbackNames(file, "wav"));
      if (action === "opus") requestDownload(fileDownloadName(file, "opus"), 0, fallbackNames(file, "opus"));
      if (action === "raw") requestDownload(file.name, 0, [file.name]);
      if (action === "delete") deleteOneFile(file);
    });

  }

  async function openBleSession(device) {
    state.device = device;
    device.addEventListener("gattserverdisconnected", onDisconnected);
    state.server = await device.gatt.connect();
    const service = await state.server.getPrimaryService(UUIDS.service);
    state.writeChar = await service.getCharacteristic(UUIDS.write);
    state.notifyChar = await service.getCharacteristic(UUIDS.notify);
    state.notifyChar.addEventListener("characteristicvaluechanged", (event) => {
      parsers.ae22.push(new Uint8Array(event.target.value.buffer));
    });
    await state.notifyChar.startNotifications();
    log("OK", "AE22 Notify 已订阅。");

    try {
      state.keyNotifyChar = await service.getCharacteristic(UUIDS.keyNotify);
      state.keyNotifyChar.addEventListener("characteristicvaluechanged", (event) => {
        parsers.ae23.push(new Uint8Array(event.target.value.buffer));
      });
      await state.keyNotifyChar.startNotifications();
      log("OK", "AE23 Notify 已订阅。");
    } catch (error) {
      log("WARN", `AE23 Notify 未启用：${error.message}`);
    }
  }

  async function connect(compatScan) {
    if (!navigator.bluetooth) {
      log("ERR", "当前浏览器不支持 Web Bluetooth，请使用 Chrome/Edge。");
      return;
    }
    try {
      setBusy(true);
      log("INFO", compatScan ? "开始兼容扫描，手动选择 QS668 后再发现 AE20 服务。" : "开始扫描广播包含 AE20 服务的设备。");
      const options = compatScan
        ? { acceptAllDevices: true, optionalServices: [UUIDS.service] }
        : { filters: [{ services: [UUIDS.service] }], optionalServices: [UUIDS.service] };
      const device = await navigator.bluetooth.requestDevice(options);
      await withTimeout(openBleSession(device), 9000, "蓝牙连接超时");
      log("OK", "设备连接成功，所有设备均可测试。");
      setConnected(true);
      setBusy(false);
      runSafeSmoke().catch((error) => {
        log("WARN", `自动巡检未完成：${error.message}`);
      });
    } catch (error) {
      log("ERR", `连接失败：${error.message}`);
      if (state.device?.gatt?.connected) state.device.gatt.disconnect();
      onDisconnected();
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    try {
      if (state.device?.gatt?.connected) state.device.gatt.disconnect();
    } finally {
      onDisconnected();
    }
  }

  function onDisconnected() {
    clearTimeout(state.listIdleTimer);
    if (state.download?.idleTimer) clearTimeout(state.download.idleTimer);
    state.connected = false;
    state.server = null;
    state.writeChar = null;
    state.notifyChar = null;
    state.keyNotifyChar = null;
    setConnected(false);
    log("INFO", "设备已断开。");
  }

  async function runNamedCommand(name) {
    const now = new Date();
    const year = now.getFullYear();
    const actions = {
      syncTime: () => sendCommand(0, 0, [year & 0xff, year >> 8, now.getMonth() + 1, now.getDate(), now.getHours(), now.getMinutes(), now.getSeconds()]),
      capacity: () => sendCommand(0, 1),
      battery: () => sendCommand(0, 3),
      firmware: () => sendCommand(0, 10),
      auth: () => sendCommand(0, 12),
      list: () => requestFileList(),
      rtStart: () => startRealtime(),
      rtPause: () => sendCommand(1, 3, [1]),
      rtResume: () => sendCommand(1, 3, [0]),
      rtStop: () => sendCommand(1, 2),
      recStart: () => sendCommand(3, 1),
      recSave: () => sendCommand(3, 3),
      recPause: () => sendCommand(3, 5),
      recResume: () => sendCommand(3, 7),
      recState: () => sendCommand(3, 19),
      recTime: () => sendCommand(3, 21),
      recName: () => sendCommand(3, 23),
      getGain: () => sendCommand(3, 25),
    };
    await actions[name]?.();
  }

  async function runSafeSmoke() {
    const steps = [
      ["电量", () => sendCommand(0, 3)],
      ["容量", () => sendCommand(0, 1)],
      ["固件", () => sendCommand(0, 10)],
      ["授权码", () => sendCommand(0, 12)],
      ["录音状态", () => sendCommand(3, 19)],
      ["录音时间", () => sendCommand(3, 21)],
      ["当前文件名", () => sendCommand(3, 23)],
      ["增益", () => sendCommand(3, 25)],
      ["文件列表", () => requestFileList()],
    ];
    for (const [label, action] of steps) {
      log("INFO", `巡检：${label}`);
      await action();
      await sleep(260);
    }
  }

  async function requestFileList() {
    state.files = [];
    renderFiles();
    clearTimeout(state.listIdleTimer);
    await sendCommand(2, 0);
    armListIdleFinish();
  }

  async function requestDownloadFromInput() {
    const name = els.downloadName.value.trim();
    const offset = Math.max(0, Number(els.downloadOffset.value || 0));
    if (!name) {
      log("WARN", "请先输入文件名。");
      return;
    }
    await requestDownload(name, offset, [name]);
  }

  async function requestDownload(name, offset, candidates) {
    const queue = unique([name, ...(candidates || [])]).filter(Boolean);
    state.download = {
      requestedName: name,
      currentName: queue.shift(),
      offset,
      candidates: queue,
      chunks: [],
      bytes: 0,
      started: false,
      idleTimer: null,
      startedAt: Date.now(),
    };
    updateDownloadStatus(`请求导入 ${state.download.currentName}，offset=${offset}`);
    await sendImportRequest(state.download.currentName, offset);
    armDownloadTimeout();
  }

  async function requestNextDownloadCandidate(reason) {
    const session = state.download;
    if (!session || session.candidates.length === 0 || session.bytes > 0) return false;
    const nextName = session.candidates.shift();
    session.currentName = nextName;
    session.chunks = [];
    session.bytes = 0;
    session.started = false;
    session.startedAt = Date.now();
    updateDownloadStatus(`${reason}，尝试 ${nextName}`);
    await sendImportRequest(nextName, session.offset);
    armDownloadTimeout();
    return true;
  }

  async function sendImportRequest(filename, offset) {
    const params = concatBytes([u32LE(offset), fixedTextBytes(filename, 24)]);
    const frame = buildFrame(new Uint8Array([2, 2, ...params]), true);
    log("TX", `2-2 导入请求整帧单写 ${frame.length}B filename=${filename} raw=${hex(frame)}`);
    await writeBytes(frame);
  }

  async function requestSegmentDownload() {
    const name = els.downloadName.value.trim();
    if (!name) {
      log("WARN", "请先输入文件名。");
      return;
    }
    const start = Math.max(0, Number(els.segStart.value || 0));
    const end = Math.max(0, Number(els.segEnd.value || 0));
    const params = concatBytes([u32LE(start), u32LE(end), textBytes(name)]);
    state.download = {
      requestedName: name,
      currentName: name,
      offset: start,
      candidates: [],
      chunks: [],
      bytes: 0,
      started: false,
      idleTimer: null,
      startedAt: Date.now(),
    };
    updateDownloadStatus(`请求分段导入 ${name} ${start}-${end}`);
    await sendCommand(2, 12, params);
    armDownloadTimeout();
  }

  async function deleteOneFile(file) {
    if (!els.allowDelete.checked) {
      log("WARN", "删除命令已拦截：请先勾选“允许发送删除命令”。");
      return;
    }
    const ok = window.confirm(`确认删除设备文件：${file.name} ?`);
    if (!ok) return;
    await sendCommand(2, 8, file.rawEntry);
  }

  async function deleteAllFiles() {
    if (!els.allowDelete.checked) {
      log("WARN", "删除全部命令已拦截：请先勾选“允许发送删除命令”。");
      return;
    }
    const ok = window.confirm("确认删除设备内全部文件？此操作不可撤销。");
    if (!ok) return;
    await sendCommand(2, 9);
  }

  async function startRealtime() {
    state.realtime = { name: "", chunks: [], bytes: 0, active: true };
    els.rtName.textContent = "--";
    els.rtBytes.textContent = "0 B";
    els.saveRealtimeBtn.disabled = true;
    await sendCommand(1, 0);
  }

  function saveRealtimeAudio() {
    if (!state.realtime.bytes) return;
    const blob = new Blob(state.realtime.chunks, { type: "application/octet-stream" });
    const name = state.realtime.name || `qs668-realtime-${timestampName()}.opus`;
    downloadBlob(blob, name);
  }

  async function sendRawCommand() {
    try {
      const type = Number(els.rawType.value);
      const cmd = Number(els.rawCmd.value);
      const params = parseHex(els.rawParams.value);
      await sendCommand(type, cmd, params);
    } catch (error) {
      log("ERR", `原始命令错误：${error.message}`);
    }
  }

  async function sendRawFrame() {
    try {
      const frame = parseHex(els.rawFrame.value);
      if (!frame.length) throw new Error("完整帧不能为空");
      log("TX", `RAW ${frame.length}B ${hex(frame)}`);
      await writeBytes(frame);
    } catch (error) {
      log("ERR", `完整帧错误：${error.message}`);
    }
  }

  async function sendCommand(type, cmd, params = []) {
    if (!state.connected || !state.writeChar) {
      log("WARN", "尚未连接设备。");
      return;
    }
    const paramBytes = params instanceof Uint8Array ? params : new Uint8Array(params);
    const data = new Uint8Array(2 + paramBytes.length);
    data[0] = type & 0xff;
    data[1] = cmd & 0xff;
    data.set(paramBytes, 2);
    const frame = buildFrame(data);
    log("TX", `${type}-${cmd} ${frame.length}B ${hex(frame)}`);
    await writeBytes(frame);
  }

  async function writeBytes(bytes) {
    if (!state.writeChar) throw new Error("写入特征未就绪");
    if (state.writeChar.writeValueWithoutResponse) {
      await state.writeChar.writeValueWithoutResponse(bytes);
    } else {
      await state.writeChar.writeValue(bytes);
    }
  }

  function handleFrame(frame) {
    const data = frame.data;
    log("RX", `${frame.source} seq=${frame.seq} len=${data.length} data=${hex(data)}`);
    if (data.length === 0) return;
    if (data.length === 1) {
      log("ACK", `${frame.source} TYPE=${data[0]}`);
      return;
    }
    const type = data[0];
    const cmd = data[1];
    const body = data.slice(2);
    if (type === 0) handleControl(cmd, body);
    else if (type === 1) handleRealtime(cmd, body);
    else if (type === 2) handleFile(cmd, body);
    else if (type === 3) handleRecord(cmd, body, frame.source);
    else log("INFO", `未知 TYPE=${type} CMD=${cmd} body=${hex(body)}`);
  }

  function handleControl(cmd, body) {
    if (cmd === 2 && body.length >= 8) {
      const remain = readU32LE(body, 0);
      const total = readU32LE(body, 4);
      els.capacityText.textContent = `${formatBytes(remain * 1024)} / ${formatBytes(total * 1024)}`;
      log("OK", `容量 remain=${remain}KB total=${total}KB`);
    } else if (cmd === 4 && body.length >= 1) {
      els.batteryText.textContent = body[0] === 110 ? "充电中" : `${body[0]}%`;
      log("OK", `电量 ${els.batteryText.textContent}`);
    } else if (cmd === 11) {
      const version = decodeText(body);
      els.firmwareText.textContent = version || "--";
      log("OK", `固件版本 ${version}`);
    } else if (cmd === 13) {
      const authCode = formatAuthCode(body);
      log("OK", `授权码 ASCII="${decodeText(body)}" HEX=${hex(body)}`);
    } else {
      log("INFO", `控制应答 CMD=${cmd} body=${hex(body)}`);
    }
  }

  function handleRealtime(cmd, body) {
    if (cmd === 0) {
      const name = decodeText(body);
      state.realtime.name = name || `qs668-realtime-${timestampName()}.opus`;
      els.rtName.textContent = state.realtime.name;
      log("OK", `实时音频文件名 ${state.realtime.name}`);
    } else if (cmd === 1) {
      state.realtime.chunks.push(body);
      state.realtime.bytes += body.length;
      els.rtBytes.textContent = formatBytes(state.realtime.bytes);
      els.saveRealtimeBtn.disabled = state.realtime.bytes === 0;
    } else if (cmd === 4 && body.length >= 1) {
      const text = ["继续", "暂停", "停止"][body[0]] || `未知(${body[0]})`;
      log("OK", `实时状态 ${text}`);
      if (body[0] === 2) state.realtime.active = false;
    } else {
      log("INFO", `实时应答 CMD=${cmd} body=${hex(body)}`);
    }
  }

  function handleFile(cmd, body) {
    if (cmd === 1) {
      parseFileList(body);
      armListIdleFinish();
    } else if (cmd === 3) {
      const name = decodeText(body);
      if (state.download) {
        state.download.started = true;
        if (name) state.download.currentName = name;
        updateDownloadStatus(`开始导入 ${state.download.currentName}`);
      }
      log("OK", `开始导入 ${name || ""}`);
      armDownloadTimeout();
    } else if (cmd === 4) {
      if (!state.download) {
        log("WARN", `收到文件数据但没有下载会话，${body.length}B 已忽略。`);
        return;
      }
      state.download.chunks.push(body);
      state.download.bytes += body.length;
      updateDownloadStatus(`接收中 ${formatBytes(state.download.bytes)}`);
      armDownloadTimeout();
    } else if (cmd === 5) {
      const code = body[0] ?? 3;
      handleImportEnd(code);
    } else if (cmd === 10) {
      log(body[0] === 0 ? "OK" : "ERR", `删除全部应答 ${deleteResultText(body[0])}`);
    } else if (cmd === 11) {
      log("OK", "终止导入应答。");
      updateDownloadStatus("已终止导入");
      state.download = null;
    } else if (cmd === 13) {
      log(body[0] === 0 ? "OK" : "ERR", `删除单个应答 ${deleteResultText(body[0])}`);
    } else if (cmd === 18) {
      clearTimeout(state.listIdleTimer);
      log("OK", `文件列表发送完毕，共 ${state.files.length} 条。`);
      renderFiles();
    } else {
      log("INFO", `文件应答 CMD=${cmd} body=${hex(body)}`);
    }
  }

  function handleRecord(cmd, body, source) {
    const resultCommands = { 2: "开始录音", 4: "保存录音", 6: "暂停录音", 8: "继续录音", 28: "设置增益" };
    if (resultCommands[cmd]) {
      const ok = cmd === 28 ? body[0] === 0 : body[0] === 1;
      log(ok ? "OK" : "ERR", `${source} ${resultCommands[cmd]}结果 ${ok ? "成功" : "失败"} code=${body[0]}`);
      return;
    }
    if (cmd === 20 && body.length >= 1) {
      els.recordStateText.textContent = recordStateText(body[0]);
      log("OK", `录音状态 ${els.recordStateText.textContent}`);
    } else if (cmd === 22 && body.length >= 6) {
      const duration = readU16LE(body, 0);
      const size = readU32LE(body, 2);
      els.recordTimeText.textContent = `${formatDuration(duration)} / ${formatBytes(size)}`;
      log("OK", `录音时间 ${duration}s currentSize=${size}`);
    } else if (cmd === 24) {
      const name = decodeText(body);
      els.downloadName.value = name || els.downloadName.value;
      log("OK", `当前文件名 ${name}`);
    } else if (cmd === 26 && body.length >= 1) {
      els.gainText.textContent = gainText(body[0]);
      els.gainSelect.value = String(body[0]);
      log("OK", `增益 ${els.gainText.textContent}`);
    } else if ([1, 3, 5, 7].includes(cmd)) {
      log("INFO", `${source} 机身事件 CMD=${cmd} ${recordEventText(cmd)}`);
    } else {
      log("INFO", `录音应答 CMD=${cmd} body=${hex(body)}`);
    }
  }

  function parseFileList(body) {
    if (body.length < 4) {
      log("WARN", `文件列表帧太短：${hex(body)}`);
      return;
    }
    const count = readU32BE(body, 0);
    let offset = 4;
    let parsed = 0;
    for (let i = 0; i < count && offset + 28 <= body.length; i += 1) {
      const entry = body.slice(offset, offset + 28);
      const durationOrTime = readU32BE(entry, 0);
      const size = readU32BE(entry, 4);
      const nameBytes = entry.slice(8, 28);
      const name = decodeText(nameBytes);
      state.files.push({ durationOrTime, size, name, rawEntry: entry });
      parsed += 1;
      offset += 28;
    }
    log("OK", `收到文件列表帧 count=${count} parsed=${parsed} total=${state.files.length}`);
    renderFiles();
  }

  function handleImportEnd(code) {
    const session = state.download;
    const text = importEndText(code);
    if (!session) {
      log("INFO", `导入结束 ${text}`);
      return;
    }
    clearTimeout(session.idleTimer);
    if (code === 0) {
      const bytes = concatBytes(session.chunks);
      const name = session.currentName || session.requestedName || `qs668-${timestampName()}.bin`;
      const wav = inspectWav(bytes);
      const suffix = wav.ok ? `，WAV ${wav.sampleRate}Hz ${wav.bitsPerSample}bit ${wav.channels}ch` : "";
      log("OK", `导入完成 ${name} ${formatBytes(bytes.length)}${suffix}`);
      updateDownloadStatus(`完成 ${formatBytes(bytes.length)}${suffix}`);
      const mime = wav.ok ? "audio/wav" : "application/octet-stream";
      const blob = new Blob([bytes], { type: mime });
      showDownloadResult(blob, name, wav.ok);
      state.download = null;
      return;
    }
    log("ERR", `导入结束 ${text}，已接收 ${formatBytes(session.bytes)}`);
    requestNextDownloadCandidate(text).then((used) => {
      if (!used) {
        updateDownloadStatus(`${text}，已接收 ${formatBytes(session.bytes)}`);
        state.download = null;
      }
    });
  }

  function renderFiles() {
    if (!state.files.length) {
      els.fileRows.innerHTML = '<tr><td colspan="5" class="empty">暂无文件列表数据</td></tr>';
      return;
    }
    els.fileRows.innerHTML = state.files.map((file, index) => {
      const time = fileTimeText(file.durationOrTime);
      return `<tr>
        <td>${index + 1}</td>
        <td>${escapeHtml(file.name)}</td>
        <td>${escapeHtml(time)}</td>
        <td>${formatBytes(file.size)}</td>
        <td>
          <div class="row-actions">
            <button data-action="wav" data-index="${index}">WAV</button>
            <button data-action="opus" data-index="${index}">OPUS</button>
            <button data-action="raw" data-index="${index}">原名</button>
            <button data-action="delete" data-index="${index}">删除</button>
          </div>
        </td>
      </tr>`;
    }).join("");
  }

  function armListIdleFinish() {
    clearTimeout(state.listIdleTimer);
    state.listIdleTimer = setTimeout(() => {
      if (state.files.length) {
        log("OK", `列表空闲收尾，共 ${state.files.length} 条。`);
        renderFiles();
      }
    }, 1200);
  }

  function armDownloadTimeout() {
    if (!state.download) return;
    clearTimeout(state.download.idleTimer);
    state.download.idleTimer = setTimeout(async () => {
      const session = state.download;
      if (!session) return;
      const used = await requestNextDownloadCandidate("导入空闲超时");
      if (!used) {
        log("ERR", `导入超时，已接收 ${formatBytes(session.bytes)}。`);
        updateDownloadStatus(`超时，已接收 ${formatBytes(session.bytes)}`);
        state.download = null;
      }
    }, 12000);
  }

  function buildFrame(data, preserveSeq = false) {
    const seq = preserveSeq ? state.seq : state.seq;
    state.seq = (state.seq + 1) & 0xff;
    const lenBytes = u16LE(data.length);
    const crc = crc16Xmodem(concatBytes([lenBytes, data]));
    const frame = new Uint8Array(6 + data.length);
    frame[0] = 0x5a;
    frame[1] = seq;
    frame[2] = crc & 0xff;
    frame[3] = (crc >> 8) & 0xff;
    frame.set(lenBytes, 4);
    frame.set(data, 6);
    return frame;
  }

  function buildFrameWithSeq(seq, data) {
    const lenBytes = u16LE(data.length);
    const crc = crc16Xmodem(concatBytes([lenBytes, data]));
    const frame = new Uint8Array(6 + data.length);
    frame[0] = 0x5a;
    frame[1] = seq & 0xff;
    frame[2] = crc & 0xff;
    frame[3] = crc >> 8;
    frame.set(lenBytes, 4);
    frame.set(data, 6);
    return frame;
  }

  function runSelfTest() {
    const lines = [];
    const check = (label, ok, detail) => lines.push(`${ok ? "PASS" : "FAIL"}  ${label}${detail ? `  ${detail}` : ""}`);

    const vector = textBytes("123456789");
    check("CRC-16/XMODEM 标准向量", crc16Xmodem(vector) === 0x31c3, `actual=0x${toHex16(crc16Xmodem(vector))}`);

    const data = concatBytes([new Uint8Array([2, 2]), u32LE(0), fixedTextBytes("note20260710-162938.wav", 24)]);
    const example = buildFrameWithSeq(3, data);
    const expected = parseHex("5a 03 9e 20 1e 00 02 02 00 00 00 00 6e 6f 74 65 32 30 32 36 30 37 31 30 2d 31 36 32 39 33 38 2e 77 61 76 00");
    check("2-2 成功下载示例帧", hex(example) === hex(expected), hex(example));

    const parserHits = [];
    const parser = new FrameParser("TEST", (frame) => parserHits.push(frame));
    parser.push(example.slice(0, 11));
    parser.push(example.slice(11));
    check("流式解析半帧重组", parserHits.length === 1 && parserHits[0].data.length === 30);

    const listBody = concatBytes([
      u32BE(1),
      u32BE(12),
      u32BE(3456),
      fixedTextBytes("note20260710-162938.", 20),
    ]);
    const before = state.files.length;
    const parsed = [];
    const oldFiles = state.files;
    state.files = parsed;
    parseFileList(listBody);
    check("文件列表 BE 解析", parsed.length === 1 && parsed[0].durationOrTime === 12 && parsed[0].size === 3456);
    state.files = oldFiles.slice(0, before);
    renderFiles();

    els.selfTestOutput.textContent = lines.join("\n");
    log("INFO", `协议自检完成：${lines.filter((line) => line.startsWith("PASS")).length}/${lines.length} 通过。`);
  }

  function crc16Xmodem(bytes) {
    let crc = 0x0000;
    for (const b of bytes) {
      crc ^= b << 8;
      for (let i = 0; i < 8; i += 1) {
        crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) : (crc << 1);
        crc &= 0xffff;
      }
    }
    return crc & 0xffff;
  }

  function setConnected(connected) {
    state.connected = connected;
    els.bleState.textContent = connected ? `已连接 ${state.device?.name || ""}`.trim() : "未连接";
    els.bleState.className = connected ? "pill ok" : "pill";
    els.connectBtn.disabled = connected;
    els.compatConnectBtn.disabled = connected;
    els.disconnectBtn.disabled = !connected;
    document.querySelectorAll("button").forEach((button) => {
      if ([
        "connectBtn", "compatConnectBtn", "disconnectBtn", "selfTestBtn", "clearLogBtn", "exportLogBtn",
      ].includes(button.id)) return;
      if (button.id === "deleteAllBtn") button.disabled = !connected;
      else button.disabled = !connected;
    });
    els.sendRawCmdBtn.disabled = !connected;
    els.sendRawFrameBtn.disabled = !connected;
  }

  function setBusy(busy) {
    els.connectBtn.disabled = busy || state.connected;
    els.compatConnectBtn.disabled = busy || state.connected;
    els.connectBtn.textContent = busy ? "连接中..." : "连接设备";
  }

  function updateSecureState() {
    const supported = Boolean(navigator.bluetooth);
    const secure = window.isSecureContext;
    els.secureState.textContent = supported && secure ? "环境可用" : "需 Chrome/HTTPS";
    els.secureState.className = supported && secure ? "pill ok" : "pill warn";
  }

  function updateDownloadStatus(text) {
    els.downloadStatus.textContent = text;
  }

  function showDownloadResult(blob, filename, playable) {
    const previous = document.querySelector(".download-result");
    if (previous) previous.remove();
    const node = document.getElementById("downloadTemplate").content.cloneNode(true);
    const url = URL.createObjectURL(blob);
    const link = node.getElementById("downloadLink");
    link.href = url;
    link.download = filename;
    link.textContent = `保存 ${filename}`;
    const audio = node.getElementById("audioPreview");
    if (playable) {
      audio.src = url;
      audio.hidden = false;
    }
    els.downloadStatus.after(node);
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function inspectWav(bytes) {
    if (bytes.length < 44) return { ok: false };
    const riff = ascii(bytes.slice(0, 4));
    const wave = ascii(bytes.slice(8, 12));
    if (riff !== "RIFF" || wave !== "WAVE") return { ok: false };
    const declared = readU32LE(bytes, 4) + 8;
    const channels = readU16LE(bytes, 22);
    const sampleRate = readU32LE(bytes, 24);
    const bitsPerSample = readU16LE(bytes, 34);
    return { ok: declared === bytes.length, declared, channels, sampleRate, bitsPerSample };
  }

  function fileDownloadName(file, ext) {
    const name = file.name;
    if (name.endsWith(".")) return `${name}${ext}`;
    if (/\.(wav|opus)$/i.test(name)) return name.replace(/\.(wav|opus)$/i, `.${ext}`);
    return `${name}.${ext}`;
  }

  function fallbackNames(file, firstExt) {
    const otherExt = firstExt === "wav" ? "opus" : "wav";
    return [fileDownloadName(file, firstExt), fileDownloadName(file, otherExt), file.name];
  }

  function log(level, message) {
    const line = `[${new Date().toLocaleTimeString()}] ${level.padEnd(4)} ${message}`;
    state.logs.push(line);
    if (state.logs.length > 1200) state.logs.shift();
    if (els.log) {
      els.log.textContent = state.logs.join("\n");
      els.log.scrollTop = els.log.scrollHeight;
    }
  }

  function clearLog() {
    state.logs = [];
    els.log.textContent = "";
  }

  function exportLog() {
    const blob = new Blob([state.logs.join("\n")], { type: "text/plain;charset=utf-8" });
    downloadBlob(blob, `qs668-ble-log-${timestampName()}.txt`);
  }

  function parseHex(input) {
    const cleaned = input.replace(/0x/gi, "").replace(/[^0-9a-fA-F]/g, "");
    if (!cleaned) return new Uint8Array(0);
    if (cleaned.length % 2) throw new Error("HEX 长度必须为偶数");
    const out = new Uint8Array(cleaned.length / 2);
    for (let i = 0; i < out.length; i += 1) out[i] = parseInt(cleaned.slice(i * 2, i * 2 + 2), 16);
    return out;
  }

  function fixedTextBytes(text, length) {
    const bytes = textBytes(text);
    const out = new Uint8Array(length);
    out.set(bytes.slice(0, length));
    return out;
  }

  function textBytes(text) {
    return new TextEncoder().encode(text);
  }

  function decodeText(bytes) {
    const end = bytes.indexOf(0);
    const data = end >= 0 ? bytes.slice(0, end) : bytes;
    return new TextDecoder("utf-8", { fatal: false }).decode(data).trim();
  }

  function formatAuthCode(bytes) {
    const text = decodeText(bytes);
    if (text && /^[\x20-\x7E]+$/.test(text)) return text;
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function ascii(bytes) {
    return Array.from(bytes, (b) => String.fromCharCode(b)).join("");
  }

  function concatBytes(parts) {
    const total = parts.reduce((sum, part) => sum + part.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    for (const part of parts) {
      out.set(part, offset);
      offset += part.length;
    }
    return out;
  }

  function u16LE(value) {
    return new Uint8Array([value & 0xff, (value >> 8) & 0xff]);
  }

  function u32LE(value) {
    return new Uint8Array([value & 0xff, (value >> 8) & 0xff, (value >> 16) & 0xff, (value >> 24) & 0xff]);
  }

  function u32BE(value) {
    return new Uint8Array([(value >> 24) & 0xff, (value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff]);
  }

  function readU16LE(bytes, offset) {
    return bytes[offset] | (bytes[offset + 1] << 8);
  }

  function readU32LE(bytes, offset) {
    return (bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16) | (bytes[offset + 3] << 24)) >>> 0;
  }

  function readU32BE(bytes, offset) {
    return (((bytes[offset] << 24) >>> 0) | (bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3]) >>> 0;
  }

  function hex(bytes) {
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(" ");
  }

  function toHex16(value) {
    return value.toString(16).padStart(4, "0");
  }

  function formatBytes(value) {
    if (!Number.isFinite(value)) return "--";
    const units = ["B", "KB", "MB", "GB"];
    let n = value;
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i += 1;
    }
    return `${n >= 10 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
  }

  function formatDuration(seconds) {
    const s = Math.max(0, seconds | 0);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rest = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(rest).padStart(2, "0")}` : `${m}:${String(rest).padStart(2, "0")}`;
  }

  function fileTimeText(value) {
    if (value > 946684800 && value < 4102444800) {
      return new Date(value * 1000).toLocaleString();
    }
    return formatDuration(value);
  }

  function recordStateText(value) {
    return ({ 1: "录音中", 2: "未录音", 3: "暂停" })[value] || `未知(${value})`;
  }

  function recordEventText(cmd) {
    return ({ 1: "开始录音", 3: "保存录音", 5: "暂停录音", 7: "继续录音" })[cmd] || "未知";
  }

  function gainText(value) {
    return ({ 1: "低", 2: "中", 3: "高" })[value] || `未知(${value})`;
  }

  function importEndText(code) {
    return ({ 0: "完成", 1: "文件不存在", 2: "offset 过大", 3: "其他停止" })[code] || `未知(${code})`;
  }

  function deleteResultText(code) {
    return code === 0 ? "成功" : `失败 code=${code}`;
  }

  function unique(items) {
    return [...new Set(items)];
  }

  function compactId(value) {
    const text = String(value || "");
    if (!text) return "--";
    return text.length > 28 ? `${text.slice(0, 12)}...${text.slice(-8)}` : text;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function withTimeout(promise, ms, message) {
    let timer = null;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
  }

  function timestampName() {
    return new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "-");
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[ch]);
  }
})();
