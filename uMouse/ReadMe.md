# uMouse SDK

本文档提供[**安徽声云**](https://www.sinicloud.com/)智能鼠标上位机 **SDK**

## SDK

### SDK 包含的模块

| Module      | Status                       |
|-------------|------------------------------|
| MouseCommon | :white_check_mark: Available |
| BleMouse    | :soon: Coming soon           |
| UsbMouse    | :white_check_mark: Available |

### 模块的提供方式和调用规则

| OS type             | Modules                          | Dynamic linked library |
|---------------------|----------------------------------|------------------------|
| Windows (x64 Only)  | Module.h, Module.lib, Module.dll | Module.dll             |
| Linux (x64 & arm64) | Module.h, libModule.so           | libModule.so           |
| Mac (x64 & arm64)   | **Currently no sdk**             | **No DLLs**            |

1. SDK 提供方式：
  - 均以**动态库**和 **C 语言头文件**的方式提供，支持任意语言调用，但**浏览器调用硬件可能受限**
  - 具体到对应系统的提供方式，请参考上述表格
2. SDK 依赖库和运行时的提供方式：
  - **Windows 依赖库**会被放置在和**动态库**的同一目录内，对于运行时**exe**安装后删除此文件即可
  - **非 Windows 依赖库**我们会提供对应的安装命令，用户**仅在确认模块缺少的情况下执行**
3. SDK 调用规范：
  - 只有**触发式通知回调**，需要用户在上位机程序启动后第一时间**主动注册处理器**
  - 对于**启动/停止**、**初始化/释放**的调用，请在分别在上位机程序**启动后/退出前各调用一次**即可
  - 其他**无需注册即可直接调用**的接口，可以在应用程序**启动后到退出前**的任意时刻**主动调用**

### 调用规范示例代码

```cpp
void RegisterCallbacks()
{
    RegisterXxxCallback(XxxHandler);    // XxxHandler 是函数指针，是你自己的业务逻辑处理器
    /* ... Other handlers ... */        // 处理器内部可为空，但上报序列号的处理器必须处理好鉴权
}

void AppStart()
{
    RegisterCallbacks();                // 应用程序启动后，请在第一时间内注册好所有的处理器
    InitPlatform();                     // 初始化特定操作系统相关的功能模块
    EngineRecognitionStart();           // 启动你自己的语音识别相关引擎，该引擎不由我们提供
    StartAudioDecodedThread();          // 启动音频解码线程，来自于 MouseCommon 组件
    StartUsbCheckThread();              // 启动上位机 USB 检测线程，来自于 UsbMouse 组件
    StartBleCheckThread();              // 启动上位机低功耗蓝牙检测线程，来自于 BleMouse 组件
    /* ... Other steps ... */           // 你自己的其他业务逻辑，比如需要启动的管道通讯等
}

void AppEnd()
{
    /* ... Other steps ... */           // 你自己的其他业务逻辑，比如需要停止的管道通讯等
    StopBleCheckThread();               // 停止上位机低功耗蓝牙检测线程，来自于 BleMouse 组件
    StopUsbCheckThread();               // 停止上位机 USB 检测线程，来自于 UsbMouse 组件
    StopAudioDecodedThread();           // 停止音频解码线程，来自于 MouseCommon 组件
    EngineRecognitionStop();            // 停止你自己的语音识别相关引擎，该引擎不由我们提供
    UninitPlatform();                   // 释放特定操作系统相关的功能模块
}
```

### 解码后的音频数据的参数

| Item         | Parameters   |
|--------------|--------------|
| Audio Format | PCM raw data |
| Sample Rate  | 16000Hz      |
| Channels     | Mono         |
| Bit Depth    | 16-bit       |

## SDK 支持的功能定制

### 事件码

| Event code | Description                                | Customizable            |
|------------|--------------------------------------------|-------------------------|
| 1          | Speech recognition (In progress)           | :warning: Limited       |
| 2          | Speech translation (In progress)           | :warning: Limited       |
| 3          | Speech searching by M key (In progress)    | :warning: Limited       |
| 4          | Speech command by AI key (In progress)     | :warning: Limited       |
| 5          | Clicked M key as a Backspace key           | :white_check_mark: Yes  |
| 6          | Clicked M key as an Enter key              | :white_check_mark: Yes  |
| 7          | Clicked M key to open a predefined link    | :white_check_mark: Yes  |
| 8 & 9      | Currently reserved                         | :warning: Limited       |
| 10         | User stops speaking                        | :x: No                  |
| 11         | Request speech recognition for all content | :warning: Limited       |
| 12         | Request speech translation for all content | :warning: Limited       |
| 13         | Request speech searching for all content   | :warning: Limited       |
| 14         | Request speech executing for all content   | :warning: Limited       |
| 15 ~ 20    | Currently reserved                         | :warning: Limited       |
| > 20       | Clicked M key once for customized function | :white_check_mark: Free |

### 鼠标按键功能及其定制

1. 事件码主要分为以下几类：
  - SDK 已经占用和保留使用的事件码：0~20
  - **用户可以定制的事件码**：
    - 对语音数据处理：**只能使用 1 到 4 以及对应的 11 到 14 的事件码**
    - 对按键单击或按住不松相当于连续多次单击的处理：建议优先使用 >20 的事件码
  - 语音数据和事件码的特殊使用说明：
    - 用户可以重定义事件码数值的使用意义，但是**语音事件码只能用于语音数据**
    - 用户最终处理对象：一般情况下是对语音开始到结束的全部完整数据做处理
    - 语音数据流式处理：只有语音流式识别、录制等功能，是对每一包语音数据做处理
    - 进行中的**每包语音数据事件码**：x，x 取值范围是 1~4，比如我们是**打字**、**翻译**、**搜索**、**命令**
    - 本次语音识别**最后一包语音数据事件码**：(x + 10)，+10 是结束标记
2. 智能鼠标多按键固定功能说明：
  - 用户长按语音功能的按键：会触发用户注册的音频数据解码完毕后的处理器（每包语音数据都触发）
    - 用户长按翻译键：语音数据里可以得到事件码必定是 **2** 或表示最后一包的 **12**
    - 用户长按 AI 键：语音数据里可以得到事件码必定是 **4** 或表示最后一包的 **14**
  - 用户操作 M 键（可以是单击，或者是语音类长按到松开的操作）：
    - 当 M 键被设置为**语音数据事件码**，则只会触发用户注册的音频数据解码完毕后的处理器
    - 当 M 键被设置为**按键事件码**，则只会触发用户注册的 M 键单击一次的处理器（**至少 1 次**）
  - 用户单击一次其他按键：触发用户的注册的对应按键处理器
3. 智能鼠标多按键可定制功能说明：
  - 所有注册的处理器：用户可以自行编写处理器逻辑，决定语音数据或按键要实现的功能
  - 用户可以使用接口 ```SetMKeyEvent``` 对支持定制的按键设置事件码
  - 支持定制的按键：只有**语音键**（只允许设置语音类事件码）和**M 键**（可设置所有事件码）
  - 对于特殊的 AI 键：**只允许定制专属命令**，即要识别的特定独有简短词组（**需额外付费**）
  - 当用户长按语音键：语音数据会返回用户设置的语音事件码，分别为 **x** 或表示最后一包的 **(x + 10)**
4. uMouse 自己的功能定制举例：
  - UI 界面里的单选按钮支持设置**语音键**的工作模式为：**1**或**2**：
  	- **1**：对返回的语音数据做语音流式识别，将识别的内容通过剪贴板粘贴实现打字
  	- **2**：将最终返回的全部语音数据做一次性识别，再调用翻译接口得到结果
  - M 键：
    - **3**：将最终返回的全部语音数据做一次性识别，再用浏览器的百度搜索对应内容给用户
    - **5**：用户按一下 M 键，就会执行一次类似用户按下 Backspace 的操作
5. 用户自己编写定制功能的场景举例：
  - 语音键或 M 键设置为**1**：对清唱的歌声做语言识别，给歌词配上对应的出现时间形成**歌词文件**
  - M 键设置为 7：要求打开 360 安全卫士或类似软件清理电脑垃圾
  注意：对于像清理电脑垃圾这种**耗时较长的事情**，用户多次按下 M 键应当**只在没有清理或清理完成后才触发一次**

## 跨平台中间件

1. **MouseCommon**：该组件提供智能鼠标和上位机双向通讯的接口，无需额外协议，主要涵盖：
  - 鼠标连接状态通知与序列号上报：上位机**必须**编写上报序列号的**登录鉴权**处理
  - 按键监听和响应：这部分接口全部以 **RegisterXxxCallback** 的形式提供，作用是直接回调上位机功能
  - 音频数据处理：包含启动和终止音频数据传递线程，以及音频解码数据的回调
  - 跨平台操作系统处理：涵盖不同操作系统的初始化，和程序退出前的释放，以及模拟打字接口
  - 特定操作系统独有拓展：
    - **Windows**：模拟用户打字功能
    - **Linux**：模拟用户打字功能
    - **Mac**：时间关系，目前没考虑
2. 硬件相关的上位机模块：这些模块，均依赖于 **MouseCommon**，且专注于和智能鼠标通讯
  - **BleMouse**：提供**低功耗蓝牙**相关的音频数据处理和按键主动设置处理
  - **UsbMouse**：提供 **USB** 相关的音频数据处理和按键主动设置处理
3. 对于出现的其他模块，属于上述模块的依赖模块，请和上述模块放在同一目录中，额外处理办法如下：
  - **Windows**：只支持 **Win10** 或以上 **64 位**系统，用户双击安装 **vc_redist.x64.exe** 后删除该文件
  - **Linux**：只支持 **Debian 10** 或以上的桌面系统，请确保存在如下依赖项（**依赖项都在 -y 后面**）：
    ```bash
    sudo apt install -y libhidapi-libusb0 libusb-1.0-0 libusb-0.1-4
    # 请确保 Linux 系统包含如下模块，如果没有则需要安装
    sudo apt install -y libx11-6 libxft2 libxtst6 libxkbfile1 libxkbcommon0 libfontconfig1 libxext6 libxfixes3
    # [UOS V20 | 银河麒麟 V10 SP1] 系统专属模块，如果没有则需要安装
    sudo apt intstall -y libavcodec58
    # [中科方德 V5.0 pro] 系统专属模块，如果没有则需要安装
    sudo apt intstall -y libavcodec59
    ```
  - **Mac**：暂时不支持

### MouseCommon API

1. 鼠标连接状态通知与序列号上报：
  ```cpp
  /*!
   *  @brief      智能鼠标成功连接后的通知回调（不代表序列号已经上报）
   *  @details    [可选]上位机可自行编写弹窗提示，如“设备已连接”、“鼠标已连接”
   *
   *  @param[out] deviceType，表示具体连接的硬件类型，如"usb"或"ble"
   */
  typedef void (*ConnectedCallback)(const char* deviceType);
  /*!
   *  @brief      智能鼠标断开后的通知回调
   *  @details    [可选]上位机可自行编写弹窗提示，如“设备已断开”、“鼠标已断开”
   *
   *  @param[out] deviceType，表示具体连接的硬件类型，如"usb"或"ble"
   */
  typedef void (*DisconnectedCallback)(const char* deviceType);
  /*!
   *  @brief      上报智能鼠标序列号的通知回调
   *  @details    [必选]上位机必须编写登录鉴权步骤
   *  @warning    智能鼠标序列号用于上位机登录鉴权，鉴权失败则无法使用智能体软件
   *
   *  @param[out] sn，智能鼠标序列号
   *  @param[out] batteryLevel，智能鼠标内置电池当前百分比电量
   *  @param[out] deviceType，表示具体连接的硬件类型，如"usb"或"ble"
   */
  typedef void (*UploadSnCallback)(const char* sn, int batteryLevel, const char* deviceType);

  /*!
   *  @brief      上位机注册智能鼠标已连接的通知回调
   *
   *  @param[in]  callback，智能鼠标已连接的处理函数
   */
  LIB_COMMON_EXPORT void RegisterConnectedCallback(ConnectedCallback callback);
  /*!
   *  @brief      上位机注册智能鼠标已断开的通知回调
   *
   *  @param[in]  callback，智能鼠标已断开的处理函数
   */
  LIB_COMMON_EXPORT void RegisterDisconnectedCallback(DisconnectedCallback callback);
  /*!
   *  @brief      上位机注册上报智能鼠标序列号的通知回调
   *  @warning    [必选]上位机必须编写登录鉴权步骤
   *
   *  @param[in]  callback，上报序列号的处理函数
   */
  LIB_COMMON_EXPORT void RegisterUploadSnCallback(UploadSnCallback callback);
  ```
2. 按键监听和响应：
  ```cpp
  /*!
   *  @brief      智能鼠标通过按键改变 DPI 的通知回调
   *  @details    [可选]上位机可自行编写更新 DPI 数值显示
   *
   *  @param[out] dpiLevel，指示 DPI 档位，并非真实的 DPI 数值
   */
  typedef void (*DpiChangedCallback)(int dpiLevel);
  /*!
   *  @brief      语音键长按按下的通知回调
   *  @details    [可选]上位机可自行编写显示语音识别中间结果的无焦点弹窗
   *
   *  @param[out] eventCode，音频用途类型，参考 Common Events 事件码
   */
  typedef void (*SpeechKeyDownCallback)(int eventCode);
  /*!
   *  @brief      语音键从长按中松开的通知回调
   *  @details    [可选]上位机可自行编写隐藏语音识别中间结果的无焦点弹窗
   */
  typedef void (*SpeechKeyUpCallback)();
  /*!
   *  @brief      AI 键单击一次的通知回调
   *  @details    [可选]上位机可自行编写将智能体主窗体从隐藏于后台的状态变为显示状态
   */
  typedef void (*AIKeyClickedCallback)();
  /*!
   *  @brief      M 键按住不放时，每次定时触发的通知回调
   *  @details    [可选]上位机可自行编写 M 键模拟用户多次按下回车键的处理
   *
   *  @param[out] indexMKey，表示第几个 M 键被按下，从 1 开始
   */
  typedef void (*MKeyHeldCallback)(int indexMKey);

  /*!
   *  @brief      上位机注册智能鼠标通过按键改变 DPI 的通知回调
   *
   *  @param[in]  callback，DPI 改变后的处理函数
   */
  LIB_COMMON_EXPORT void RegisterDpiChangedCallback(DpiChangedCallback callback);

  /*!
   *  @brief      上位机注册智能鼠标语音键长按按下的通知回调
   *
   *  @param[in]  callback，语音键长按按下的处理函数
   */
  LIB_COMMON_EXPORT void RegisterSpeechKeyDownCallback(SpeechKeyDownCallback callback);
  /*!
   *  @brief      上位机注册智能鼠标语音键从长按中松开的通知回调
   *
   *  @param[in]  callback，语音键从长按中松开的处理函数
   */
  LIB_COMMON_EXPORT void RegisterSpeechKeyUpCallback(SpeechKeyUpCallback callback);
  /*!
   *  @brief      上位机注册智能鼠标 AI 键单击一次的通知回调
   *
   *  @param[in]  callback，AI 键单击一次的处理函数
   */
  LIB_COMMON_EXPORT void RegisterAIKeyClickedCallback(AIKeyClickedCallback callback);
  /*!
   *  @brief      上位机注册智能鼠标 M 键按住不放时，每次定时触发的通知回调
   *
   *  @param[in]  callback，M 键按住不放时，每次定时触发的处理函数
   */
  LIB_COMMON_EXPORT void RegisterMKeyHeldCallback(MKeyHeldCallback callback);

  /*!
   *  @brief      通过事件码设置 M 键用途
   *  @details    比如语音打字、翻译，模拟用户按下后退、回车键等
   *  @warning    indexMKey 为 0 时，特指设置的是语音键，N 表示设置第 N 个 M 键
   *
   *  @param[in]  eventCode，事件码表示用途，请参考 Common Events 事件码
   *  @param[in]  indexMKey，表示对第几个 M 键做设置，普通鼠标只有 1 个 M 键
   */
  LIB_COMMON_EXPORT void SetMKeyEvent(int eventCode, int indexMKey);
  ```
3. 音频数据处理：
  ```cpp
  /*!
   *  @brief      音频数据结构体
   *  @details    用于封装音频事件的数据负载，包含音频数据及其用途
   */
  typedef struct AudioData
  {
      int _eventCode;             //!< 音频用途类型，参考 Common Events 事件码
      unsigned char* _pData;      //!< 音频二进制数据（可以是编码前后的数据）
      int _length;                //!< 音频二进制数据有效长度
  }AudioData;

  /*!
   *  @brief      音频数据解码完毕后的通知回调
   *  @details    [可选]上位机可自行编写音频数据用于语音识别的处理
   */
  typedef void (*AudioDataDecodedCallback)(const AudioData* pAudioData);

  /*!
   *  @brief      上位机注册智能鼠标音频数据解码完毕后的通知回调
   *
   *  @param[in]  callback，音频数据解码完毕后的处理函数
   */
  LIB_COMMON_EXPORT void RegisterAudioDataDecodedCallback(AudioDataDecodedCallback callback);
  /*!
   *  @brief      启动音频解码线程
   */
  LIB_COMMON_EXPORT void StartAudioDecodedThread();
  /*!
   *  @brief      停止音频解码线程
   */
  LIB_COMMON_EXPORT void StopAudioDecodedThread();
  ```
4. 跨平台操作系统处理：
  ```cpp
  /*!
   *  @brief      操作系统相关功能使用前的平台初始化调用
   *  @details    比如 Windows 注册热键，Linux 模拟键盘打字
   */
  LIB_COMMON_EXPORT void InitPlatform();
  /*!
   *  @brief      操作系统相关功能使用完成后的平台释放调用
   */
  LIB_COMMON_EXPORT void UninitPlatform();

  /*!
   *  @brief      模拟用户按下回车键
   */
  LIB_COMMON_EXPORT void SendReturn();
  /*!
   *  @brief      模拟用户按下后退键
   *
   *  @param[in]  count，表示连续按下后退键的次数
   */
  LIB_COMMON_EXPORT void SendBackspace(int count);
  /*!
   *  @brief      发送常用键盘热键命令，比如 Ctrl + v
   *  @param[in]  letter，字母按键，从 'a' 到 'z'
   *  @warning    注意 Mac 上的是 Command 按键，不是 Ctrl 按键
   */
  LIB_COMMON_EXPORT void SendCtrlCommand(char letter);
  ```
5. 特定操作系统独有拓展：
  - Windows 系统：
    - 模拟用户打字
      ```cpp
      /*!
       *  @brief      模拟用户键盘打字，在光标后追加打字的内容
       *  @param[in]  text，Unicode 编码的文字内容
       */
      LIB_COMMON_EXPORT void SendText(const wchar_t* text);
      /*!
       *  @brief      模拟用户从后向前删除紧跟着光标前指定的文字内容
       *  @param[in]  text，Unicode 编码的文字内容
       */
      LIB_COMMON_EXPORT void DeleteText(const wchar_t* text);
      ```
  - Linux 系统
    - 模拟用户打字
      ```cpp
      /*!
       *  @brief      模拟用户键盘打字，在光标后追加打字的内容
       *  @param[in]  text，utf8 编码的文字内容
       */
      LIB_COMMON_EXPORT void SendText(const char* text);
      /*!
       *  @brief      模拟用户从后向前删除紧跟着光标前指定的文字内容
       *  @param[in]  text，Unicode 编码的文字内容
       */
      LIB_COMMON_EXPORT void DeleteText(const char* text);
      ```

### BleMouse

```cpp
/*!
 *  @brief      启动上位机低功耗蓝牙检测线程
 */
LIB_EXPORT void StartBleCheckThread();
/*!
 *  @brief      停止上位机低功耗蓝牙检测线程
 */
LIB_EXPORT void StopBleCheckThread();

/*!
 *  @brief      通过低功耗蓝牙设置鼠标 DPI
 *
 *  @param[in]  dpiLevel，鼠标 DPI 挡位，需参考对应硬件手册
 */
LIB_EXPORT bool SetBleMouseDpi(int dpiLevel);
```

### UsbMouse

```cpp
/*!
 *  @brief      启动 USB 检测线程
 */
LIB_EXPORT void StartUsbCheckThread();
/*!
 *  @brief      停止 USB 检测线程
 */
LIB_EXPORT void StopUsbCheckThread();

/*!
 *  @brief      通过 USB 设置鼠标 DPI
 *
 *  @param[in]  dpiLevel，鼠标 DPI 挡位，需参考对应硬件手册
 */
LIB_EXPORT bool SetUsbMouseDpi(int dpiLevel);
```
