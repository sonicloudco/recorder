import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import 'logger.dart';
import 'model_transfer.dart';

///
/// @author ChenQiang
/// @fileName service_transfer_files.dart
/// @date 2025/12/15
/// @company yyc
/// @project SoniNote
/// @description 蓝牙文件传输任务的「临时状态管理器」
///

class TransferFileService {
  static const String _transferDirName = 'bt_transfer';
  //本次任务的“文件清单”
  static const String _indexFileName = 'index.json';
  //获取（并确保存在）本次蓝牙传输任务的根目录
  static Future<Directory> _getTransferDir() async {
    //调试完成换成dbDirectoryPath
    final cacheDir = await getApplicationCacheDirectory();

    final dir = Directory('${cacheDir.path}/$_transferDirName');
    // +    final cacheDir = AppDirectoryManager.dbDirectoryPath;
    // +    final dir = Directory('$cacheDir/$_transferDirName');

    // Log.d('💾保存的路径dir=$dir');
    if (!await dir.exists()) {
      await dir.create(recursive: true);
    }
    return dir;
  }

  //获取 index.json 文件对象
  static Future<File> _getIndexFile() async {
    final dir = await _getTransferDir();
    return File('${dir.path}/$_indexFileName');
  }

  //获取某一个文件对应的「进度状态文件」
  static Future<File> _getFileStateFile(String fileId) async {
    final dir = await _getTransferDir();
    return File('${dir.path}/$fileId.json');
  }

  /// 保存传输任务到 JSON 文件（每次获取录音笔文件列表时覆盖）
  static Future<void> saveTransferState({
    required List<TransferFileMeta> files,
  }) async {
    try {
      final indexFile = await _getIndexFile();
      final indexData = {
        'files': files.map((e) => e.toJson()).toList(),
        'time': DateTime.now().millisecondsSinceEpoch,
      };
      await indexFile.writeAsString(jsonEncode(indexData));
      Log.d('💾 保存 index.json 成功');
    } catch (e) {
      Log.e('❌ 保存 index.json 失败: $e');
    }
  }

  /// 更新单个文件的传输进度
  static Future<void> updateFileProgress({
    required String fileName,
    required TransferFileState state,
  }) async {
    try {
      final stateFile = await _getFileStateFile(fileName);
      await stateFile.writeAsString(jsonEncode(state.toJson()));
    } catch (e) {
      Log.e('❌ 更新文件进度失败: $e');
    }
  }

  /// 保存云端任务ID到文件状态
  static Future<void> updateTaskId(String fileName, int taskId) async {
    try {
      final stateFile = await _getFileStateFile(fileName);
      if (await stateFile.exists()) {
        final json = jsonDecode(await stateFile.readAsString());
        final state = TransferFileState.fromJson(json);
        final updatedState = TransferFileState(
          path: state.path,
          size: state.size,
          status: state.status,
          taskId: taskId,
          uploadedChunks: state.uploadedChunks,
        );
        await stateFile.writeAsString(jsonEncode(updatedState.toJson()));
        Log.d('✅ 已保存taskId: $taskId 到 $fileName.json');
      }
    } catch (e) {
      Log.e('❌ 保存taskId失败: $e');
    }
  }


  /// 判断某个文件的传输状态文件是否存在
  static Future<bool> isFileStateExist(String fileName) async {
    try {
      final stateFile = await _getFileStateFile(fileName);
      return await stateFile.exists();
    } catch (e) {
      Log.e('❌ 检查文件是否存在失败: $e');
      return false;
    }
  }

  /// 获取单个文件传输状态
  static Future<TransferFileState> getFileState(String fileName) async {
    try {
      final stateFile = await _getFileStateFile(fileName);
      Log.d(
        "stateFile.readAsString()=${jsonDecode(await stateFile.readAsString())}",
      );
      if (!await stateFile.exists()) {
        return TransferFileState.initial();
      }
      return TransferFileState.fromJson(
        jsonDecode(await stateFile.readAsString()),
      );
    } catch (e) {
      Log.e('❌ 获取文件状态失败: $e');
      return TransferFileState.initial();
    }
  }

  /// 删除单个文件的传输状态（传输完成时调用）
  static Future<void> deleteFileState(String fileName) async {
    try {
      final stateFile = await _getFileStateFile(fileName);
      if (await stateFile.exists()) {
        await stateFile.delete();
        Log.d('🗑️ 已删除文件状态: $fileName.json');
      }
    } catch (e) {
      Log.e('❌ 删除文件状态失败: $e');
    }
  }

  /// 仅删除index.json（传输任务全部完成后调用）
  static Future<void> deleteIndexFile() async {
    try {
      final indexFile = await _getIndexFile();
      if (await indexFile.exists()) {
        await indexFile.delete();
        Log.d('✅ 已删除 index.json');
      }
    } catch (e) {
      Log.e('❌ 删除 index.json 失败: $e');
    }
  }

  /// 查询状态为uploading的文件名列表
  static Future<List<String>> queryPendingUploadFiles() async {
    try {
      final dir = await _getTransferDir();
      if (!await dir.exists()) return [];

      final files = dir.listSync();
      List<String> pendingFiles = [];

      for (var file in files) {
        if (file.path.endsWith('.json') && !file.path.endsWith('index.json')) {
          try {
            final content = await File(file.path).readAsString();
            final json = jsonDecode(content);
            final state = TransferFileState.fromJson(json);
            if (state.status == TransferStatus.uploading ||
                state.status == TransferStatus.converting ||
                state.status == TransferStatus.chunkUploading) {
              final fileName = file.path
                  .split('/')
                  .last
                  .replaceAll('.json', '');
              pendingFiles.add(fileName);
            }
          } catch (e) {
            Log.e('❌ 读取文件状态失败: ${file.path}, $e');
          }
        }
      }
      return pendingFiles;
    } catch (e) {
      Log.e('❌ 查询待上传文件失败: $e');
      return [];
    }
  }

  static Future<void> clearTransferState() async {
    try {
      final dir = await _getTransferDir();
      if (await dir.exists()) {
        await dir.delete(recursive: true);
        Log.d('✅ 已清除所有传输状态文件');
      }
    } catch (e) {
      Log.e('❌ 清除传输状态失败: $e');
    }
  }
}
