
///
/// @author ChenQiang
/// @fileName services_transfer_model.dart
/// @date Tue Dec 16 2025 17:37:15
/// @company yyc
/// @project SoniNote
/// @description
///

enum TransferStatus {
  pending,
  downloading,
  uploading,
  converting,
  chunkUploading,
  completed,
  failed
}


extension FileNameParse on String {
  /// 从 note20251203-123344.opus 解析 DateTime
  DateTime toDateTime() {
    final name = split('.').first; // note20251203-123344
    final pure = name.replaceAll(RegExp(r'^[a-zA-Z]+'), '');
    final parts = pure.split('-');

    if (parts.length != 2) {
      throw FormatException('Invalid file name: $this');
    }

    final datePart = parts[0]; // 20251203
    final timePart = parts[1]; // 123344

    return DateTime(
      int.parse(datePart.substring(0, 4)),
      int.parse(datePart.substring(4, 6)),
      int.parse(datePart.substring(6, 8)),
      int.parse(timePart.substring(0, 2)),
      int.parse(timePart.substring(2, 4)),
      int.parse(timePart.substring(4, 6)),
    );
  }

  /// 转换为业务真实类型
  int toRealType() {
    final lower = toLowerCase();
    if (lower.startsWith('call')) return 4;
    if (lower.startsWith('note')) return 3;
    return 2;
  }
}


class TransferFileMeta {
  final String name;
  final int size;
  final String durationMs;

  /// 文件创建时间（从文件名解析得到）
  final DateTime createdAt;

  /// 录音类型 0手机文件 1是手机麦克风录音 2录音笔录音 3卡片机麦克风收音 4是卡片机听筒收音
  final int recordingType;

  TransferFileMeta({
    required this.name,
    required this.size,
    required this.durationMs,
    required this.createdAt,
    required this.recordingType,
  });

  factory TransferFileMeta.fromJson(Map<String, dynamic> json) {
    final fileName = json['name']?.toString() ?? '';

    return TransferFileMeta(
      name: fileName,
      size: int.tryParse(json['size']?.toString() ?? '0') ?? 0,
      durationMs: json['time'] ?? 0,
      createdAt: fileName.isNotEmpty
          ? fileName.toDateTime()
          : DateTime.fromMillisecondsSinceEpoch(0),
      recordingType: fileName.isNotEmpty ? fileName.toRealType() : 0,
    );
  }

  Map<String, dynamic> toJson() => {
        'name': name,
        'size': size,
        'time': durationMs,
        'createdAt': createdAt.millisecondsSinceEpoch,
        'recordingType': recordingType,
      };

}

class TransferFileState {
  final String path; //文件保存路径
  final int size; //文件总大小
  final TransferStatus status; //下载状态
  final int? taskId; //云端任务ID
  final int uploadedChunks; //已上传分片数量

  TransferFileState({
    required this.path,
    required this.size,
    required this.status,
    this.taskId,
    this.uploadedChunks = 0,
  });

  factory TransferFileState.initial() {
    return TransferFileState(
      path: '',
      size: 0,
      status: TransferStatus.pending,
      uploadedChunks: 0,
    );
  }

  factory TransferFileState.fromJson(Map<String, dynamic> json) {
    return TransferFileState(
      path: json['path'] ?? '',
      size: int.tryParse(json['size']?.toString() ?? '0') ?? 0,
      status: TransferStatus.values.firstWhere(
        (e) => e.name == json['status'],
        orElse: () => TransferStatus.pending,
      ),
      taskId: json['taskId'],
      uploadedChunks: json['uploadedChunks'] ?? 0,
    );
  }

  Map<String, dynamic> toJson() => {
        'path': path,
        'size': size,
        'status': status.name,
        'taskId': taskId,
        'uploadedChunks': uploadedChunks,
      };
}
