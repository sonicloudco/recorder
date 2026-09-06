import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:soni_sdk_demo/service_bluetooth.dart';
import 'package:soni_sdk_demo/bluetooth_constants.dart';
import 'package:soni_sdk_demo/service_record.dart';
import 'package:soni_sdk_demo/service_system_ability.dart';
import 'package:wifi_iot/wifi_iot.dart';

import 'logger.dart';

///
/// @author 杰森明
/// @fileName provider_record_pen.dart
/// @date 2025/12/10 11:21
/// @description [RecordPenProvider]
/// 录音笔接口
/// @description [flutter与生层通信协议]
///

/// Opus 音频数据来源：实时录音 / 文件传输
class AudioDataType {
  static const record = 'record';
  static const file = 'file';
}

class RecordPenProvider extends ChangeNotifier with WidgetsBindingObserver {
  // 🔧 服务实例（抱歐）
  final _bluetoothService = RecordPenBluetoothService();
  final _recordService = RecordPenRecordService();

  // String transFileName = "note20260109-090905.opus"; // 46分钟
  // = "call20260115-174208.opus"; // 6分钟
  late String _transFileName;

  String get transFileName => _transFileName;

  // 连接成功后是否自动传输
  final bool _isAutoTransfer = false;

  setTransFileName(String fileName) {
    _transFileName = fileName;
  }

  // 前后台状态
  bool _isAppForeground = true;

  bool get isAppForeground => _isAppForeground;

  /// 待跳转标记
  bool _pendingGotoRealTimePen = false;

  bool get pendingGotoRealTimePen => _pendingGotoRealTimePen;

  final MethodChannel _methodChannel = const MethodChannel(
    'com.soni.soni_sdk_demo/recordPen',
  );
  final EventChannel _eventChannel = const EventChannel(
    'com.soni.soni_sdk_demo/eventChannel',
  );
  StreamController<Map<String, dynamic>> streamController =
      StreamController.broadcast();

  bool _isConnected = false;

  //是否正在录音
  bool _isRecording = false;

  //启动录音当时的时间
  int? _pendingRecordStartTimestamp;

  int? get pendingRecordStartTimestamp => _pendingRecordStartTimestamp;

  bool get isConnected => _isConnected;

  // bool get isRecording => _isRecording;
  String _cbc = "-1";

  String get cbc => _cbc;
  String _sn = "00000000";

  String get sn => _sn;

  /// 设备信息（统一管理）
  Map<String, dynamic>? _deviceInfo;

  Map<String, dynamic>? get deviceInfo => _deviceInfo;

  String deviceName = "";
  String deviceAddress = "";

  final List<Map<String, String>> _discoveredDevices = [];

  List<Map<String, String>> get discoveredDevices =>
      List.unmodifiable(_discoveredDevices);

  /// WiFi 状态：0表示打开，1表示关闭或失败，2表示连接成功，3表示断开
  String _wifiState = "";

  String get wifiState => _wifiState;
  String _wifiName = "";

  String get wifiName => _wifiName;

  /// 设备固件版本
  String _deviceVersion = "";

  String get deviceVersion => _deviceVersion;

  /// 设备固件版本 Code（数字格式）
  int _deviceVersionCode = 0;

  int get deviceVersionCode => _deviceVersionCode;

  void clearPendingJump() {
    _pendingGotoRealTimePen = false;
    _pendingRecordStartTimestamp = null;
  }

  void triggerPendingJump() {
    if (_pendingGotoRealTimePen && onGotoRealTimePen != null) {
      _pendingGotoRealTimePen = false;
      onGotoRealTimePen!();
    }
  }

  //连接成功后判断是否跳转到录音页的回调，isRecording表示跳转前是否已经开始录音了
  void Function(bool isRecording)? connectGotoRealTimePen;

  void Function()? onGotoRealTimePen;

  //通知是否传输完成
  void Function(int state, String? progress)? onTransferComplete;

  //所有文件下载完成回调（触发云端列表刷新）
  void Function()? onAllTransferComplete;

  ///  构造函数
  RecordPenProvider() {
    WidgetsBinding.instance.addObserver(this);
  }

  ///  全局只初始化一次
  ///  确保有权限
  init() async {
    Log.d("录音笔初始化");
    try {
      if (Platform.isAndroid) {
        await _methodChannel.invokeMethod('init');
      }
    } catch (e) {}

    _eventChannel.receiveBroadcastStream().listen(
      (event) async {
        await _handleBluetoothEvent(event);
      },
      onError: (err) {
        Log.e("$err");
      },
    );
  }

  bool _isAudioBytes(dynamic data) =>
      data is List || data is Uint8List || data is TypedData;

  List<int> _toIntList(dynamic data) {
    if (data is Uint8List) return data.toList();
    if (data is List) return List<int>.from(data);
    return List<int>.from((data as TypedData).buffer.asUint8List());
  }

/// 发送音频帧的方法
///
/// 该方法将音频数据分割并添加到流控制器中，以便后续处理或传输
///
/// @param audioType 音频类型，用于标识音频数据的格式或编码方式
/// @param data 音频数据，以字节列表形式提供
  void _emitAudioFrames(String audioType, List<int> data) {
    /// A 款：80ms/160字节，一包 4 帧，每帧 40 字节（20ms）
    /// B 款：解密后 400 字节，一包 5 帧，每帧 80 字节
    for (final item in _splitData(data)) {
      streamController.add({"audioType": audioType, "audio": item});
    }
  }

  List<List<int>> _splitData(List<int> data) {
    final blockSize = deviceAes256Gcm ? 80 : 40;
    final result = <List<int>>[];

    int index = 0;
    while (index + blockSize <= data.length) {
      final block = data.sublist(index, index + blockSize);
      result.add(block);
      index += blockSize;
    }

    return result;
  }

  /// 处理蓝牙事件的主方法
  Future<void> _handleBluetoothEvent(dynamic event) async {
    try {
      // 预处理事件数据（修复Android/iOS平台差异 + 修复JSON格式）
      // String processedEvent = RecordPenUtils.preprocessBluetoothEvent(
      //   event.toString(),
      // );

      if (event is Map) {
        final audioType = event['audioType']?.toString();
        final audioData = event['audio'];
        if (audioType != null && _isAudioBytes(audioData)) {
          _emitAudioFrames(audioType, _toIntList(audioData));
          return;
        }
        if (event.containsKey('sdkLog')) {
          streamController.add({'SDK': event['sdkLog']});
          return;
        }
        print("sdklog=>$event");
        return;
      } else if (event is List<int>) {
        /// 兼容旧版原生：未带类型时按实时录音处理
        _emitAudioFrames(AudioDataType.record, event);
        return;
      } else if (event is Uint8List) {
        _emitAudioFrames(AudioDataType.record, event.toList());
        return;
      } else if (event is String && (event.startsWith('send:')|| event.startsWith('rev:'))) {
        streamController.add({'SDK': event});
        return;
      } else {
        print("录音笔数据源${event.runtimeType}:$event");
      }

      // 解析 JSON
      Map<String, dynamic> map = jsonDecode(event as String);
      streamController.add(map);
      // 根据 cmd 分发到不同的处理方法
      final String cmd = map['cmd'].toString();
      switch (cmd) {
        case "1":
          _handleDeviceSearchEvent(map);
          break;
        case "2":
          await _handleDeviceConnectionEvent(map);
          break;
        case "3":
          _handleRecordStateEvent(map);
          break;
        case "4":
          await _handleFileListEvent(map);
          break;
        case "5":
          await _handleTransferProgressEvent(map);
          break;
        case "6":
          _handleBatteryEvent(map);
          break;
        case "7":
          _handleDeviceSNEvent(map);
          break;
        case "8":
          _handleDeviceButtonEvent(map);
          break;
        case "9":
          _handleRecordingStateEvent(map);
          break;
        case "10":
          _handleRecordTimeEvent(map);
          break;
        case "11":
          _handleRecordFileNameEvent(map);
          break;
        case "12":
          _handleDeviceWiFiStateEvent(map);
          break;
        case "14":
          _handleDeleteEvent(map);
          break;
        case "17":
          _handleDeviceFirewareVersion(map);
          break;
        case "18":
          _handleDeviceWiFiCode(map);
          break;
        case "19":
          //返回状态，0表示已经进入升级状态，
          //     1 表示不能进入升级状态，主要原因是可能是SD卡或者内存不足；
          //     2 表示WiFi的TCP/IP未连接，升级模式需要在WiFi连接的状态下才能进行升级；
          streamController.add({
            "是否可以升级": map["data"]["prepareState"] == "0" ? "可以" : "不可以",
          });
          break;
        case "20":
        //state    标记：0 表示正常，非0表示异常
        //     progress    当前进度字节；
        //     total    总字节数
        case "21":
          //state    标记：0 表示正常，非0表示异常
          //     receiveState  0：文件接收完成，准备重启
          break;
        case "22":
          //state    标记：0 表示正常，非0表示异常
          //     low_power_state    0 不是  1是
          break;
        case "23":
          /// 设置低功耗录音模式状态返回
          /// low_power_state=0 表示成功，=1 表示失败
          final data = map['data'] as Map<String, dynamic>? ?? {};
          final lowPowerState = data['low_power_state']?.toString() ?? "0";
          break;
        default:
          Log.w("未知的 cmd: $cmd");
      }
    } catch (e, t) {
      Log.e("$e $t \n ${event.runtimeType}");
    }
  }

  /// 处理设备搜索 (cmd=1)
  void _handleDeviceSearchEvent(Map<String, dynamic> map) {
    if (map['state'] != "0") return;

    final name = map['data']['name']?.toString() ?? '';
    final address = map['data']['address']?.toString() ?? '';
    deviceName = name;
    deviceAddress = address;

    if (address.isNotEmpty && address != 'null') {
      final exists = _discoveredDevices.any((d) => d['address'] == address);
      if (!exists) {
        _discoveredDevices.add({'name': name, 'address': address});
        notifyListeners();
      }
    }

    // 如果自动连接未开启，直接返回
    if (!_isAutoConnecting || deviceMap == null) return;

    final savedAddress = (deviceMap?["address"] ?? "").toString();
    final savedName = (deviceMap?["name"] ?? "").toString();

    final bool isMatch;

    // 如果保存的设备有 address → 必须用 address 匹配
    if (savedAddress.isNotEmpty) {
      // 扫描到的设备如果没有 address，直接跳过
      if (deviceAddress.isEmpty || deviceAddress == "null") {
        isMatch = false;
      } else {
        isMatch = (deviceAddress == savedAddress && deviceName == savedName);
      }
    } else {
      // 保存的设备没有 address → 老设备逻辑
      // 如果扫描到的设备存在 address（非空且不为 "null"），直接跳过
      if (deviceAddress.isNotEmpty && deviceAddress != "null") {
        isMatch = false;
      } else {
        // 扫描到的设备也没有 address，按名字匹配
        isMatch = (deviceName == savedName);
      }
    }

    // if (isMatch) {
    //   Log.d("找到匹配设备，开始自动连接name: $deviceName, address: $deviceAddress");
    //   connect({'name': deviceName, 'address': deviceAddress});
    // }
  }

  /// 处理连接状态变化 (cmd=2)
  Future<void> _handleDeviceConnectionEvent(Map<String, dynamic> map) async {
    //安卓是true,false,ios 是 1，0
    if (map['data']['connect_state'] == "true" ||
        map['data']['connect_state'] == "1") {
      String? deviceName = map['data']["name"]?.toString();
      if (deviceName != null && !deviceName.contains("null")) {
        _isConnected = true;
        // 保存设备信息
        print("保存设备信息$deviceMap");
        _deviceInfo = deviceMap;

        _isAutoConnecting = true;
        stopSearch();

        // 连接成功后
        await ServiceSystemAbility.sleep(2000);
        asyncNowTime();
        await ServiceSystemAbility.sleep(50);
        getCBC();
        await ServiceSystemAbility.sleep(50);
        getSN();
        // // ⚠️⚠️⚠️⚠️⚠️紧接着开始获取传输文件，传输蓝牙大概率断，所以需要延时3秒才可以
        // await ServiceSystemAbility.sleep(3000);
        // getRecordFileList();
      }
    } else if (map['data']['address'] == "null") {
      _isConnected = false;
    } else {
      closeSuccess();
    }
    notifyListeners();
  }

  /// 处理录音状态 (cmd=3)
  void _handleRecordStateEvent(Map<String, dynamic> map) {
    int recordState =
        int.tryParse(map['data']['record_state']?.toString() ?? '0') ?? 0;
    if (recordState == 1) {
      //设备正在录音
      print("record_state=1开始录音");
      _isRecording = true;
    } else if (recordState == 2) {
      //设备暂停录音
    } else if (recordState == 0) {
      //设备停止录音
      print("record_state=0停止录音");
      _isRecording = false;
      // deleteFile(map['data']["fileName"]);
    }
    notifyListeners();
  }

  /// 处理文件列表 (cmd=4)
  Future<void> _handleFileListEvent(Map<String, dynamic> map) async {
    List<dynamic> dataList = map['data'] ?? [];
    bool isFinish = map['finish'] == '1' || map['finish'] == 1;

    // 列表还在接收中（isFinish=false）
    if (!isFinish && dataList.isNotEmpty) {
      notifyListeners();
    }

    // 列表接收完成（isFinish=true）
    if (isFinish && _isAutoTransfer) {
      Log.d('📋 文件列表接收完成，共  个文件');
      sendFileFromPenToPhoneWithOffset(transFileName, 0);
    }
  }

  /// 处理传输进度 (cmd=5)
  Future<void> _handleTransferProgressEvent(Map<String, dynamic> map) async {
    /// 0完成，4传输中，1文件不在，2offset过大（预留），3其他停止
    final String fileState = map['data']['record_file_state'].toString();
    switch (fileState) {
      case '0':
        await _handleTransferComplete(map['data']);
        break;
      case '4':
        await _handleTransferInProgress(map['data']);
        break;
      case '1':
        await _handleFileNotFound();
        break;
      case '2':
        Log.e('❌ offset过大');
        onTransferComplete?.call(2, null);
        break;
    }
  }

  /// 处理文件传输完成 (cmd=5, state=0)
  Future<void> _handleTransferComplete(Map<String, dynamic> data) async {
    Log.d('📋 文件传输完成');
  }

  /// 处理文件传输中 (cmd=5, state=4)
  Future<void> _handleTransferInProgress(Map<String, dynamic> data) async {}

  /// 处理文件不存在 (cmd=5, state=1)
  Future<void> _handleFileNotFound() async {
    Log.e('❌ 文件不存在');
  }

  /// 处理电量 (cmd=6)
  void _handleBatteryEvent(Map<String, dynamic> map) {
    _cbc = map['data']['cbc']?.toString() ?? '-1';
    _isConnected = true;
    notifyListeners();
  }

  /// 处理设备SN (cmd=7)
  void _handleDeviceSNEvent(Map<String, dynamic> map) {
    _sn = map['data']['sn']?.toString() ?? '00000000';
    notifyListeners();
  }

  /// 处理设备按键事件 (cmd=8)
  void _handleDeviceButtonEvent(Map<String, dynamic> map) {
    String event = map['data']['event']?.toString() ?? '';
    switch (event) {
      case "1": // 开始录音
        startBtnBackRecord();
        _pendingRecordStartTimestamp = DateTime.now().millisecondsSinceEpoch;
        if (_isAppForeground) {
          onGotoRealTimePen?.call();
        } else {
          _pendingGotoRealTimePen = true;
          print("后台开始录音，保存待跳转标记");
        }
        break;

      case "3": // 停止录音
        _stopBtnBackRecord();

        if (!_isAppForeground) {
          print("后台停止录音，取消待跳转");
          clearPendingJump();
        }
        break;

      default:
    }
  }

  /// 处理录音状态 (cmd=9)
  void _handleRecordingStateEvent(Map<String, dynamic> map) {
    bool isRecording = map['data']['recordState'] == "1";
    if (isRecording) {
      print("小机在录音状态");
      _isRecording = true;
      connectGotoRealTimePen?.call(true);
    } else {
      _isRecording = false;
    }
    notifyListeners();
  }

  /// 处理录音时间 (cmd=10)
  void _handleRecordTimeEvent(Map<String, dynamic> map) {
    // _getFileName();
    notifyListeners();
  }

  /// 处理录音文件名 (cmd=11)
  void _handleRecordFileNameEvent(Map<String, dynamic> map) {
    String fileName = map['data']['recordName']?.toString() ?? '';
    print("启动获取录音名称$fileName");
  }

  _handleDeleteEvent(Map<String, dynamic> map) {
    bool isDelete = map['data']['delete_state'] == "0";
    if (isDelete) {
      print("删除成功");
    } else {
      print("删除失败");
    }
  }

  /// 前后台切换监听
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    bool wasForeground = _isAppForeground;

    if (state == AppLifecycleState.resumed) {
      _isAppForeground = true;
      print("RecordPenProvider监听到前台");
      if (wasForeground != _isAppForeground) {
        notifyListeners();
      }
      // App 前台时触发待跳转
      triggerPendingJump();
    } else if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden) {
      _isAppForeground = false;
      print("RecordPenProvider监听到后台");
      if (wasForeground != _isAppForeground) {
        notifyListeners();
      }
    }
  }

  /// 设备信息
  @Deprecated("抛弃，原生未实现")
  getDeviceInfo() async {
    await _methodChannel.invokeMethod('getDeviceInfo');
  }

  /// 搜索
  startSearch() async {
    _discoveredDevices.clear();
    deviceName = '';
    deviceAddress = '';
    notifyListeners();
    await _bluetoothService.startSearch();
  }

  /// 连接
  connect(Map<String, dynamic> device) async {
    print("链接设备name=${device["name"]} address=${device["address"]}");
    deviceMap = device;
    await _bluetoothService.connect(device);
  }

  // 用于自动连接的设备名称和地址
  Map<String, dynamic>? deviceMap;
  bool _isAutoConnecting = false;

  /// 停止自动连接状态
  void stopAutoConnect() {
    _isAutoConnecting = false;
    _deviceInfo = null;
  }

  /// 断开连接成功
  closeSuccess() async {
    Log.d("☹️断开了");
    _isConnected = false;
    _isRecording = false;
    _cbc = "-1";
    _sn = "00000000";

    notifyListeners();
    Future.delayed(const Duration(seconds: 5), () {
      if (!_isAutoConnecting) return;
      startSearch();
    });
  }

  //准备断开连接
  closeConnect() async {
    Log.d("准备断开连接");
    await _bluetoothService.disconnect();
  }

  /// 录音
  startRecord() async {
    if (_isRecording) {
      // UIUtil.showToast("已经录音，不启动startRecord()");
      return;
    }
    await _recordService.startRecord();
  }

  /// 暂停录音
  pauseRecord() async {
    await _recordService.pauseRecord();
    Log.d("暂停结果");
  }

  /// 恢复录音
  continueRecord() async {
    await _recordService.continueRecord();
  }

  /// 停止录音
  stopRecord() async {
    print("停止录音");
    await _recordService.stopRecord();
    if (!_isRecording) {
      // UIUtil.showToast("已经停止，不启动stopRecord()");

      return;
    }
    _pendingRecordStartTimestamp = null;
    if (_isRecording != false) {
      _isRecording = false;
      await _recordService.stopRecord();
    }
  }

  /// 停止扫描
  stopSearch() async {
    await _bluetoothService.stopSearch();
  }

  // ================== 私有辅助方法 ==================

  /// 获取文件列表
  Future<void> getRecordFileList() async {
    await _bluetoothService.getRecordFileList();
  }

  /// 获取设备电量
  Future<void> getCBC() async {
    await _bluetoothService.getCBC();
  }

  /// 获取sn
  Future<void> getSN() async {
    await _bluetoothService.getSN();
  }

  /// 给设备下发当前时间
  Future<void> asyncNowTime() async {
    await _bluetoothService.syncTime();
  }

  /// 获取录音笔正在录音中时候的录音文件名称
  Future<void> getFileName() async {
    await _bluetoothService.getFileNameOnlyRecording();
  }

  /// 传输文件到手机（支持断点续传）
  Future<void> sendFileFromPenToPhoneWithOffset(
    String fileName,
    int offset,
  ) async {
    await _recordService.sendFileFromPenToPhoneWithOffset(fileName, offset);
  }

  /// 停止传输文件
  Future<void> stopTransferFile(String fileName) async {
    await _recordService.stopTransferFile(fileName);
  }

  ///返回按键开始录音
  void startBtnBackRecord() {
    _recordService.startBtnBackRecord();
  }

  ///返回按键停止录音
  void _stopBtnBackRecord() {
    _isRecording = false;
    _pendingRecordStartTimestamp = null;
    _recordService.stopBtnBackRecord();
  }

  /// 是否已连接
  isDeviceConnected() {
    return _bluetoothService.isDeviceConnected();
  }

  /// 获取设备容量
  getDeviceCapacity() {
    return _bluetoothService.getDeviceCapacity();
  }

  /// 获取获取录音笔录音状态
  getRecordState() {
    return _bluetoothService.getRecordState();
  }

  ///获取当前录音时长，当录音笔设备是在录音状态的时候
  getTimeOnlyRecording() {
    return _bluetoothService.getTimeOnlyRecording();
  }

  /// 删除文件
  deleteFile(String fileName) async {
    await _bluetoothService.deleteFile(fileName);
  }

  /// 删除所有文件
  deleteAllFile() async {
    await _bluetoothService.deleteAllFiles();
  }

  /// 获取当前增益
  getDeviceGain() {
    return _bluetoothService.getDeviceGain();
  }

  /// 设置当前增益  参数：gain 值：1：低   2：中   3：高
  setDeviceGain(int gain) {
    return _bluetoothService.setDeviceGain(gain);
  }

  /// 发送sendAppShowState
  sendAppShowState(int state) {
    return _bluetoothService.sendAppShowState(state);
  }

  /// 获取设备固件版本
  getDeviceVersion() {
    return _bluetoothService.getDeviceVersion();
  }

  ///搜索 wifi
  openWiFi() async {
    _methodChannel.invokeMethod('openWiFi');

    if (!Platform.isAndroid) return;
    await WiFiForIoTPlugin.setEnabled(true, shouldOpenSettings: false);
    await WiFiForIoTPlugin.forceWifiUsage(true);
    streamController.add({"wifi": "Wi-Fi 已尝试打开"});
  }

  ///关闭搜索 Wi-Fi
  closeWiFi() async {
    _methodChannel.invokeMethod('closeWiFi');
    if (!Platform.isAndroid) return;
    await WiFiForIoTPlugin.forceWifiUsage(false);
    await WiFiForIoTPlugin.setEnabled(false, shouldOpenSettings: false);
    streamController.add({"wifi": "Wi-Fi 已尝试关闭"});
  }

  ///连接wifi
  connectDeviceWiFi() async {
    if (_wifiName.isEmpty) {
      streamController.add({"wifi": "wifiName 为空，无法匹配并连接"});
      return;
    }

    if (Platform.isAndroid) {
      await WiFiForIoTPlugin.setEnabled(true, shouldOpenSettings: false);
    }

    final bool connected = await WiFiForIoTPlugin.connect(
      _wifiName.trim(),
      joinOnce: true,
      withInternet: false,
      security: NetworkSecurity.NONE,
    );
    if(Platform.isAndroid){
      streamController.add({
        "wifi": connected ? "已发起直连: $_wifiName" : "直连失败: $_wifiName",
      });
      if (!connected) return;
    }
    if (Platform.isAndroid) {
      await WiFiForIoTPlugin.forceWifiUsage(true);
    }
    // connect() 返回 true 后，系统可能仍在完成关联流程，先确认 SSID 再发起 TCP。
    bool ssidMatched = false;
    if (Platform.isAndroid) {
      for (int i = 0; i < 12; i++) {
        await Future.delayed(const Duration(seconds: 1));
        final String? currentSsid = await WiFiForIoTPlugin.getSSID();
        Log.d("currentSsid: $currentSsid");
        final String normalized = (currentSsid ?? '')
            .replaceAll('"', '')
            .trim()
            .toLowerCase();
        final String target = _wifiName.trim().toLowerCase();
        if (normalized == target) {
          ssidMatched = true;
          break;
        }
      }
    } else {
      ssidMatched = true;
    }

    if (!ssidMatched) {
      streamController.add({"wifi": "Wi-Fi 尚未稳定连接到 $_wifiName，取消建立 TCP"});
      return;
    }
    if (Platform.isIOS) {
      await ServiceSystemAbility.sleep(8000);
    }
    streamController.add({"wifi": "建立TCP/IP"});
    await _methodChannel.invokeMethod('connectDeviceWiFi');
  }

  ///断开 Wi-Fi
  disconnectDeviceWiFi() async {
    await WiFiForIoTPlugin.disconnect();
    await WiFiForIoTPlugin.forceWifiUsage(false);
    streamController.add({"wifi": "已断开当前 Wi-Fi"});
    _methodChannel.invokeMethod('disconnectDeviceWiFi');
  }

  ///获取设备 Wi-Fi 状态
  /// 返回 WiFi 状态字符串，如果超时则返回当前缓存的状态
  /// 0表示打开，1表示关闭或失败，2表示连接成功，3表示断开
  getDeviceWiFiState() async {
    await _methodChannel.invokeMethod('getDeviceWiFiState');
  }

  ///获取设备版本 Code
  getDeviceVersionCode({Duration timeout = const Duration(seconds: 5)}) async {
    // 发送获取版本 Code 命令
    await _methodChannel.invokeMethod('getDeviceVersionCode');
  }

  /// 获取手机热点状态
  /// 返回热点状态字符串，如果超时则返回当前缓存的状态
  /// 4 有手机连接设备WiFi热点   5当前没有手机连接设备WiFi热点
  getWiFiHotspotState() async {
    await _methodChannel.invokeMethod('getWiFiHotspotState');
  }

  /// 请求设备进入 OTA 升级模式
  /// 结果通过 EventChannel (cmd=19) 返回，由 WiFiConnectionManager 处理
  Future<void> requestDeviceGotoOtaMode() async {
    await _methodChannel.invokeMethod('requestDeviceGotoOtaMode');
  }

  /// 发送 OTA 固件文件
  /// [filePath] 固件文件路径
  /// 进度通过 EventChannel (cmd=20) 返回
  Future<void> sendOtaFile(String filePath) async {
    await _methodChannel.invokeMethod('sendOtaFile', {'filePath': filePath});
  }

  /// 将 assets 内的固件文件落地到本地临时目录，返回可供原生读取的真实路径。
  ///
  /// 兼容 `assets/update.ufw` 和误写的 `assets/update.utw`。
  Future<String> _materializeOtaAssetToLocalFile({
    String preferredAssetPath = 'assets/update.ufw',
  }) async {
    final candidates = <String>[
      preferredAssetPath,
      if (preferredAssetPath != 'assets/update.ufw') 'assets/update.ufw',
      if (preferredAssetPath != 'assets/update.utw') 'assets/update.utw',
    ];

    ByteData? data;
    String? usedAssetPath;
    Object? lastError;

    for (final assetPath in candidates) {
      try {
        data = await rootBundle.load(assetPath);
        usedAssetPath = assetPath;
        break;
      } catch (e) {
        lastError = e;
      }
    }

    if (data == null || usedAssetPath == null) {
      throw FlutterError(
        '无法读取固件 assets，已尝试：${candidates.join(", ")}；lastError=$lastError',
      );
    }

    final dir = await getTemporaryDirectory();
    final ext = usedAssetPath.split('.').last;
    final fileName =
        'ota_firmware_${DateTime.now().millisecondsSinceEpoch}.$ext';
    final file = File('${dir.path}/$fileName');

    final bytes = data.buffer.asUint8List(
      data.offsetInBytes,
      data.lengthInBytes,
    );
    await file.writeAsBytes(bytes, flush: true);

    return file.path;
  }

  /// 从 assets 发送 OTA 固件到原生端（原生再读取文件字节并发送给设备）。
  Future<void> sendOtaAssetFile({
    String assetPath = 'assets/update.ufw',
  }) async {
    final localPath = await _materializeOtaAssetToLocalFile(
      preferredAssetPath: assetPath,
    );
    await sendOtaFile(localPath);
  }

  /// cmd 12
  /// WiFi 状态返回
  /// 0表示打开状态，1表示关闭或打开失败，2表示连接成功，3表示断开连接状态
  void _handleDeviceWiFiStateEvent(Map<String, dynamic> map) {
    final data = map['data'] as Map<String, dynamic>? ?? {};
    _wifiState = data['wifi_state']?.toString() ?? "";
    if (data['wifiName'] != "" && data['wifiName'] != null) {
      _wifiName = data['wifiName']?.toString() ?? "";
    }
    const wifiStateTextMap = <String, String>{
      "0": "WiFi热点打开",
      "1": "WiFi热点关闭",
      "2": "WiFi连接成功",
      "3": "WiFi断开连接",
      "4": "有手机WiFi连接",
      "5": "当前没有手机连接设备WiFi热点",
    };
    streamController.add({
      "wifi": wifiStateTextMap[_wifiState] ?? "未知WiFi状态($_wifiState)",
    });
    notifyListeners();
  }

  /// cmd 17
  /// 获取设备固件版本
  void _handleDeviceFirewareVersion(Map<String, dynamic> map) {
    _deviceVersion = map['data']['version'];
  }

  /// cmd 18
  /// 获取设备固件版本 Code
  void _handleDeviceWiFiCode(Map<String, dynamic> map) {
    final versionCodeData = map['data']['versionCode'];
    // 处理字符串或整数类型
    if (versionCodeData is int) {
      _deviceVersionCode = versionCodeData;
    } else if (versionCodeData is String) {
      _deviceVersionCode = int.tryParse(versionCodeData) ?? 0;
    } else {
      _deviceVersionCode = 0;
    }
  }

  /// 获取低功耗录音模式状态
  getDeviceLowPowerRecordMode({
    Duration timeout = const Duration(seconds: 5),
  }) async {
    await _methodChannel.invokeMethod('getDeviceLowPowerRecordMode');
  }

  /// 设置低功耗录音模式状态
  /// state: 0 表示关闭，1 表示开启
  Future<void> setDeviceLowPowerRecordMode(bool enabled) async {
    await _methodChannel.invokeMethod('setDeviceLowPowerRecordMode', {
      'state': enabled ? 1 : 0,
    });
  }

  /// sendFileTimeInterval
  void setSendFileTimeInterval(int interval) {
    _methodChannel.invokeMethod('sendFileTimeInterval', {
      'interval': interval,
    });
  }
}
