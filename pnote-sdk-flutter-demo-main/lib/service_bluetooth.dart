import 'dart:async';
import 'package:flutter/services.dart';

import 'logger.dart';

///
/// @author ChenQiang
/// @fileName service_bluetooth.dart
/// @date 2025-12-17
/// @description 蓝牙设备管理服务
/// 职责：搜索、连接、断开、获取设备信息
///

class RecordPenBluetoothService {
  final MethodChannel _methodChannel = const MethodChannel(
    'com.soni.soni_sdk_demo/recordPen',
  );

  /// 搜索设备
  Future<void> startSearch() async {
    try {
      await _methodChannel.invokeMethod('startSearch');
    } catch (e) {
      Log.e('❌ 搜索设备失败: $e');
    }
  }

  /// 连接设备
  Future<void> connect(Map<String, dynamic> device) async {
    try {
      Log.d('🔗 连接设备: name=${device["name"]}, address=${device["address"]}');
      await _methodChannel.invokeMethod('connect', {
        'name': device["name"],
        'address': device["address"],
      });
    } catch (e) {
      Log.e('❌ 连接设备失败: $e');
    }
  }

  /// 断开连接
  Future<void> disconnect() async {
    try {
      Log.d('🔌 断开连接');
      await _methodChannel.invokeMethod('close');
    } catch (e) {
      Log.e('❌ 断开连接失败: $e');
    }
  }

  /// 停止搜索
  Future<void> stopSearch() async {
    try {
      await _methodChannel.invokeMethod('stopSearch');
    } catch (e) {
      Log.e('❌ 停止搜索失败: $e');
    }
  }

  /// 获取设备电量
  Future<void> getCBC() async {
    try {
      Log.d('🔋 获取设备电量');
      await _methodChannel.invokeMethod('getCBC');
    } catch (e) {
      Log.e('❌ 获取电量失败: $e');
    }
  }

  /// 获取设备 SN
  Future<void> getSN() async {
    try {
      Log.d('📱 获取设备 SN');
      await _methodChannel.invokeMethod('getSN');
    } catch (e) {
      Log.e('❌ 获取 SN 失败: $e');
    }
  }

  /// 同步时间到设备
  Future<void> syncTime() async {
    try {
      Log.d('📱 同步时间 syncTime');
      await _methodChannel.invokeMethod('syncTime');
    } catch (e) {
      Log.e('❌ 同步时间失败: $e');
    }
  }

  /// 获取设备录音状态
  Future<void> getRecordState() async {
    try {
      await _methodChannel.invokeMethod('recordState');
    } catch (e) {
      Log.e('❌ 获取录音状态失败: $e');
    }
  }

  /// 获取文件列表
  Future<void> getRecordFileList() async {
    try {
      Log.d('📋 获取文件列表');
      await _methodChannel.invokeMethod('getRecordFileList');
    } catch (e) {
      Log.e('❌ 获取文件列表失败: $e');
    }
  }

  /// 获取录音笔正在录音中时候的录音文件名称
  Future<void> getFileNameOnlyRecording() async {
    try {
      await _methodChannel.invokeMethod('getFileName');
    } catch (e) {
      Log.e('❌ 获取文件名失败: $e');
    }
  }

  /// 获取录音时间
  Future<void> getRecordTime() async {
    try {
      await _methodChannel.invokeMethod('getRecordTime');
    } catch (e) {
      Log.e('❌ 获取录音时间失败: $e');
    }
  }

  /// 设置 ASR 语言（发送 WebSocket URL）
  Future<void> sendRealTimeUrl(String websocketUrl) async {
    try {
      await _methodChannel.invokeMethod('settingsAsrUrl', {
        'url': websocketUrl,
      });
    } catch (e) {
      Log.e('❌ 设置 ASR URL 失败: $e');
    }
  }

  /// 更换实时转写地址
  Future<void> changeUrlToConnect(String websocketUrl) async {
    try {
      await _methodChannel.invokeMethod('changeUrlToConnect', {
        'url': websocketUrl,
      });
    } catch (e) {
      Log.e('❌ 更换 ASR URL 失败: $e');
    }
  }

  /// 删除文件
  Future<void> deleteFile(String fileName) async {
    try {
      Log.d('🗑️ 删除文件: $fileName');
      await _methodChannel.invokeMethod('deleteReordFile', {
        'fileName': fileName,
      });
    } catch (e) {
      Log.e('❌ 删除文件失败: $e');
    }
  }

  /// 删除所有文件
  Future<void> deleteAllFiles() async {
    try {
      Log.d('🗑️ 删除所有文件');
      await _methodChannel.invokeMethod('deleteAllReordFile');
    } catch (e) {
      Log.e('❌ 删除所有文件失败: $e');
    }
  }

  /// 获取当前增益
  Future<void> getDeviceGain() async {
    try {
      await _methodChannel.invokeMethod('getDeviceGain');
    } catch (e) {
      Log.e('❌ 获取当前增益失败: $e');
    }
  }

  /// 设置当前增益  参数：gain 值：1：低   2：中   3：高
  Future<void> setDeviceGain(int gain) async {
    try {
      await _methodChannel.invokeMethod('setDeviceGain', {'gain': gain});
    } catch (e) {
      Log.e('❌ 设置当前增益失败: $e');
    }
  }

  /// 是否已连接
  isDeviceConnected() async {
    return await _methodChannel.invokeMethod('isDeviceConnected');
  }

  /// 获取容量
  Future<void> getDeviceCapacity() async {
    return await _methodChannel.invokeMethod('getDeviceCapacity');
  }

  /// 获取当前录音时长，当录音笔设备是在录音状态的时候
  Future<void> getTimeOnlyRecording() async {
    return await _methodChannel.invokeMethod('getTimeOnlyRecording');
  }

  /// 通知app前后台
  Future<void> notifyAppStatus(bool isForeground) async {
    return await _methodChannel.invokeMethod('notifyAppStatus', {
      'isForeground': isForeground,
    });
  }

  /// 删除所有录音文件
  Future<void> sendAppShowState(int state) async {
    try {
      await _methodChannel.invokeMethod('sendAppShowState',state);
    } catch (e) {
      Log.e('❌ 删除所有录音文件失败: $e');
    }
  }
  ///getDeviceVersion
  Future<void> getDeviceVersion() async {
    return await _methodChannel.invokeMethod('getDeviceVersion');
  }
}
