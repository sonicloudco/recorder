import 'dart:io';

import 'package:flutter/services.dart';

import 'package:permission_handler/permission_handler.dart';

import 'logger.dart';


class ServiceSystemAbility {

  static void hideStatusBar() {
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
  }

  static void showStatusBar() {
    SystemChrome.setEnabledSystemUIMode(
      SystemUiMode.manual,
      overlays: [SystemUiOverlay.top, SystemUiOverlay.bottom],
    );
  }

  /// 睡眠
  static Future<void> sleep(int milliseconds) async {
    await Future.delayed(Duration(milliseconds: milliseconds));
  }

  /// 查看权限状态
  static Future<PermissionStatus> requestPermissionStatus(
      Permission permission) async {
    PermissionStatus permissionStatus = await permission.status;
    PermissionStatus condition = PermissionStatus.denied;
    switch (permissionStatus) {
      case PermissionStatus.denied:
        condition = PermissionStatus.denied;
        break;
      case PermissionStatus.granted:
        condition = PermissionStatus.granted;
        break;
      case PermissionStatus.restricted:
        // 操作系统拒绝访问请求的特性。用户不能更改 仅ios
        condition = PermissionStatus.restricted;
        break;
      case PermissionStatus.limited:
        //  用户已授权此应用程序的有限访问。仅ios
        condition = PermissionStatus.limited;
        break;
      case PermissionStatus.permanentlyDenied:
        condition = PermissionStatus.permanentlyDenied;
        break;
      case PermissionStatus.provisional:
        condition = PermissionStatus.provisional;
        break;
    }
    Log.d('有权限吗$permission==$permissionStatus');
    return condition;
  }
}
