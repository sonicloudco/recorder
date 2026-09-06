/// A/B 录音笔开关，由打包/运行时 --dart-define=DEVICE_AES256GCM 注入。
/// false = normal：普通 Opus 流（40 字节/帧）
/// true  = bk：AES-256-GCM 解密后（80 字节/帧）
///
/// 需与 Android flavor 一致：
///   flutter run --flavor normal --dart-define=DEVICE_AES256GCM=false
///   flutter run --flavor bk --dart-define=DEVICE_AES256GCM=true
const bool deviceAes256Gcm =
    bool.fromEnvironment('DEVICE_AES256GCM', defaultValue: false);
