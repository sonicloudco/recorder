# 声云录音卡 Recorder

> 一张录音卡片，一套完整的 BLE 接入方案。

声云录音卡 Recorder 是面向硬件厂商、软件开发者和行业集成商的开源示例项目。我们以 **录音卡片硬件** 为核心产品，同时开放录音卡 BLE 通讯协议 SDK、跨平台接入示例和桌面端 Demo，帮助团队从“连接设备”快速走到“录音、传输、转写和业务落地”。

<img src="录音卡片.png" alt="声云录音卡片" width="480" style="max-width: 100%; height: auto;">

## 项目定位

录音卡通过低功耗蓝牙与手机或电脑配合使用：设备负责采集和保存音频，应用负责连接控制、文件管理、音频处理以及业务集成。

本仓库提供三类资源：

| 资源 | 适用对象 | 内容 |
| --- | --- | --- |
| 移动端 SDK | Android、iOS、鸿蒙及 Flutter 开发者 | 蓝牙搜索连接、录音控制、实时音频、文件传输、Wi-Fi、OTA、设备信息 |
| Windows/macOS Demo | PC 应用和工具开发者 | Python BLE 协议实现、命令行 REPL、Web 控制台、离线语音转文字 |
| 协议与测试资料 | 需要深度定制的团队 | BLE GATT、帧格式、CRC、文件下载、录音事件和兼容策略 |

## 能做什么

- 手机 App 远程开始、暂停、继续和保存录音
- 接收实时 OPUS 音频流，接入自有 ASR 或会议纪要服务
- 查询录音文件列表，下载 WAV/OPUS，支持续传、分段下载和删除
- 获取电量、容量、固件版本、SN、授权码、录音状态、时长和增益
- 管理设备 Wi-Fi 热点并通过 TCP 传输数据
- 通过 OTA 流程升级设备固件
- 在 Windows/macOS 上完成扫描、连接、下载、试听和转写
- 以协议层为基础扩展小程序、桌面软件和行业 App

## 目录导航

```text
recorder/
├── pnote-android-sdk/                  # Android AAR
├── pnote-ios-sdk/                      # iOS 静态库与头文件
├── pnote-harmony-sdk/                  # 鸿蒙 HAR SDK
├── pnote-sdk-flutter-demo-main/        # Flutter 联调 Demo
├── pnote-web-win&Mac-demo/             # BLE 命令行与 Web Demo
│   ├── recorder/                       # 协议、BLE、设备会话、ASR、Web 后端
│   ├── web/                            # 前端页面
│   ├── tests/                          # 协议与设备模拟测试
│   └── docs/协议.md                    # CB08 通讯协议 V1.0
├── 录音卡片.png                         # 产品图片
├── 硬件参数.png                         # 硬件参数图
├── demo.mp4                            # 演示视频
└── LICENSE                             # MIT License
```

## 五分钟运行桌面 Demo

需要 Python 3.10 以上、可用 BLE 适配器，以及一台处于可连接状态的录音卡。

```bash
cd pnote-web-win&Mac-demo
python -m venv .venv
source .venv/bin/activate                 # Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
python main.py --web
```

浏览器打开 `http://127.0.0.1:8000`。命令行也可这样验证：

```text
record> scan
record> connect 0
record> smoke
record> list
record> download 0
record> transcribe 0
```

完整命令见 [桌面 Demo README](pnote-web-win%26Mac-demo/README.md)。离线转写和 Web 页面为可选能力：

```bash
pip install -r requirements-asr.txt
pip install -r requirements-web.txt
python -m unittest discover tests -v
```

首次转写会从 ModelScope 下载约 900 MB 模型，之后可离线运行。

### pnote-web-win&Mac-demo

桌面 Demo 位于 [`pnote-web-win&Mac-demo`](pnote-web-win%26Mac-demo/)，同时提供命令行 REPL 和浏览器 Web 控制台，可完成设备扫描、连接巡检、录音控制、文件下载、WAV 试听、实时推流和离线转写。

![录音卡 BLE 控制台](pnote-web-win%26Mac-demo/%E5%BD%95%E9%9F%B3%E5%8D%A1%20ble%20%E6%8E%A7%E5%88%B6%E5%8F%B0.png)

## 移动端接入

### Flutter 联调工程

```bash
cd pnote-sdk-flutter-demo-main
flutter pub get
flutter run
```

请使用真机验证蓝牙、录音、Wi-Fi 和 OTA。核心入口是 `lib/provider_record_pen.dart`，详见 [Flutter Demo README](pnote-sdk-flutter-demo-main/README.md)。

### Android

将 [Android AAR](pnote-android-sdk/pnote_20260728171001.aar) 复制到应用的 `app/libs`（或 `src/main/libs`），添加 `fileTree` 依赖及 SDK 所需的 RxJava、Retrofit、EventBus 等依赖，然后调用：

```java
PNote.init(context, deviceDataListener);
PNote.startSearch();
PNote.connectDevice(name, address);
```

回调包括 JSON 设备事件、实时录音 OPUS 数据和文件 OPUS 数据。完整接口见 [Android SDK 开发文档](pnote-sdk-flutter-demo-main/android%20SDK开发文档(最新版本)-2026.04.23.md)。

### iOS

将 [libPNote.a](pnote-ios-sdk/libPNote.a) 与 [PNode.h](pnote-ios-sdk/PNode.h) 加入 Xcode，实现 `WindBleDelegate`，调用 `startSearch`、`connectDeviceAndAddress` 等接口。完整接口和 OTA 流程见 [iOS SDK 开发文档](pnote-sdk-flutter-demo-main/iOS%20SDK开发文档(最新版本)-2026.04.23.md)。

### 鸿蒙

鸿蒙 SDK 以 [har_recordersdk.har](pnote-harmony-sdk/har_recordersdk.har) 提供。将 HAR 引入工程后，按鸿蒙 SDK 文档完成蓝牙权限、扫描、连接和数据回调接入；如需配套示例或最新 API 说明，请联系声云获取资料包。

## 协议实现要点

- BLE Service：`0xAE20`；写特征 `AE21`，数据通知 `AE22`，按键通知 `AE23`
- 通用帧：`MAGIC(0x5A) + SEQ + CRC-16/XMODEM + LEN + TYPE/CMD/PARAMS`
- 通知数据支持半帧、多帧、噪声和跨包重组；AE22 与 AE23 使用独立解析缓存
- 文件列表按多帧组装，收到 `CMD=18` 才视为完整；旧固件按空闲超时兼容收尾
- 下载请求包含 4 字节小端 offset 和 24 字节文件名；`CMD=2-2` 整帧必须一次 GATT 写入
- 实时音频当前为 16 kHz、单声道 OPUS 数据，解码和 ASR 由业务侧选择

协议字段、命令表、真实抓包帧和兼容策略见 [docs/协议.md](pnote-web-win%26Mac-demo/docs/%E5%8D%8F%E8%AE%AE.md)。

## 集成建议

1. 先用桌面 Demo 完成扫描、连接、巡检和文件下载。
2. 再在 Flutter Demo 中验证移动端权限、回调线程、录音状态和文件落盘。
3. 将 SDK 通道层、设备会话层和业务 UI 解耦，回调先进入业务事件队列。
4. 生产环境为 OTA、删除文件和 Wi-Fi 切换增加状态机、超时、重试和版本校验。
5. 不要把云端 ASR 密钥写入客户端；实时 OPUS 数据发送到自有后端或受控服务。

## 适用场景

会议记录、采访采集、课堂培训、企业礼品、巡检取证、内容创作、智能硬件配套 App，以及需要快速验证录音硬件的软硬件联合项目。

## 硬件与商业合作

本项目的商业模式是“硬件销售 + 开放 SDK + 项目服务”：录音卡片硬件用于量产和行业应用，BLE 协议 SDK 与 Demo 用于降低研发接入成本；批量采购、外观/固件定制、行业功能、云端转写和交付支持可按项目评估。

<img src="企业微信.png" alt="企业微信" width="260" style="max-width: 100%; height: auto;">

如需获取硬件规格、样机、鸿蒙 SDK、协议完整版、企业微信二维码或技术支持资料，请联系 **安徽声云**（官网：[sinicloud.com](https://www.sinicloud.com/)）。咨询时请说明目标平台、预计数量和应用场景。

## 许可与使用边界

仓库示例代码以 [MIT License](LICENSE) 发布。AAR、静态库、HAR、固件和协议资料可能包含厂商专有内容，具体授权范围以随包说明和双方商务协议为准；请勿将设备私有协议用于未获授权的硬件或产品。

## 反馈与问题

提交问题时请附上操作系统与版本、目标平台、SDK/Demo 版本、设备固件版本、复现步骤，以及必要的日志或收发帧 hex。涉及真实设备数据时，请先脱敏序列号、地址、授权码和录音内容。
