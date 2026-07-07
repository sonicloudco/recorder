# 声云开放sdk
#### 涵盖录音、语音识别、语音合成、语音转文字、文字转语音等功能

## AI智能录音卡SDK
- 端侧录音 + 云端 ASR，会议纪要自动生成
- App 与硬件联动，素材统一归档与检索
- 适配企业礼品、教育、采访等多类场景


## 智能鼠标 SDK

SDK 说明请参阅[**此处**](uMouse/第三方接入说明.md)

### 不同系统蓝牙的最低要求

| OS type & CPU ISR   | HCI Version | OS SDK Version |
|---------------------|-------------|----------------|
| Windows (x64 Only)  | HCI 11      | 10.0.19041.0   |
| Linux (x64 & arm64) | HCI 11      | BlueZ 5.50     |
| Mac (x64 & arm64)   | --          | --     |

**注意**：部分**银河麒麟**的蓝牙 HCI 版本是 10，导致即便 BlueZ 即便是 5.50 以上也**兼容性不良**

### 对应系统的 SDK 下载

1. **Windows**：仅支持 **Windows 10** 或以上 **64 位**操作系统，[**下载**](uMouse/Win10-x64.zip)
2. **Linux**：目前只支持如下 **Debian** 桌面系统
  - **中科方德 V5.0 Pro**：仅支持 x64 CPU 的 [**amd64-sdk**](uMouse/FangDe-x64.zip)
  - **Uos V20**：包括 x64 CPU 的 [**amd64-sdk**](uMouse/UosV20-x64.zip) 和 arm64 CPU 的 [**arm64-sdk**](uMouse/UosV20-arm64.zip)
  - **银河麒麟 V10 SP1**：包括 x64 CPU 的 [**amd64-sdk**](uMouse/YinheKylinV10SP1-x64.zip) 和 arm64 CPU 的 [**arm64-sdk**](uMouse/YinheKylinV10SP1-arm64.zip)
3. **Mac**：当前支持arm64版本。
4. 有其他需求可以联系声云。

### 开源许可

本项目以 [MIT License](LICENSE) 发布。
