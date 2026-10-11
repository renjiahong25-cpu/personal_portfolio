import 'package:isar/isar.dart';
import 'package:path_provider/path_provider.dart';

import '../models/schema.dart';

class IsarService {
  IsarService({String? directory}) : _directoryOverride = directory;

  /// 测试注入：指向临时目录，避免污染真实数据；默认系统应用支持目录。
  final String? _directoryOverride;

  late Isar isar;

  Future<void> open() async {
    final directory = _directoryOverride ??
        (await getApplicationSupportDirectory()).path;
    isar = await Isar.open(
      [ChatWindowSchema, ChatMessageSchema],
      directory: directory,
    );
  }

  Future<List<ChatWindow>> loadWindows() async {
    return isar.chatWindows.where().sortByUpdatedAtDesc().findAll();
  }

  Future<ChatWindow?> loadWindow(int id) async {
    return isar.chatWindows.get(id);
  }

  Future<void> upsertWindow(ChatWindow window) async {
    await isar.writeTxn(() => isar.chatWindows.put(window));
  }

  Future<void> deleteWindow(ChatWindow window) async {
    await isar.writeTxn(() async {
      await isar.chatMessages
          .filter()
          .windowIdEqualTo(window.id)
          .deleteAll();
      await isar.chatWindows.delete(window.id);
    });
  }

  Future<List<ChatMessage>> loadMessages(int windowId) async {
    return isar.chatMessages
        .filter()
        .windowIdEqualTo(windowId)
        .sortByCreatedAt()
        .findAll();
  }

  Future<void> upsertMessage(ChatMessage message) async {
    await isar.writeTxn(() => isar.chatMessages.put(message));
  }

  Future<void> upsertMessages(List<ChatMessage> messages) async {
    await isar.writeTxn(() => isar.chatMessages.putAll(messages));
  }

  Future<void> deleteMessage(int id) async {
    await isar.writeTxn(() => isar.chatMessages.delete(id));
  }
}