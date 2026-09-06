import 'package:logger/logger.dart';

var logger = Logger();

class Log {
  Log._();

  // 获取当前时间
  static String _getNowTime() {
    return "${"${DateTime.now()}".substring(0, 19)}=>>";
  }

  static final Log instance = Log._();

  static void d(String msg) {
    logger.d(_getNowTime() + msg);
  }

  static void e(String msg) {
    logger.e(_getNowTime() + msg);
  }

  static void i(String msg) {
    logger.i(_getNowTime() + msg);
  }

  static void w(String msg) {
    logger.w(_getNowTime() + msg);
  }

  static void f(String msg) {
    logger.f(_getNowTime() + msg);
  }
}
