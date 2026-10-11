import 'package:isar/isar.dart';

part 'schema.g.dart';

@collection
class ChatWindow {
  Id id = Isar.autoIncrement;

  late String title;

  String? chatId;

  List<String> modelIds = [];

  late DateTime createdAt;

  late DateTime updatedAt;
}

@collection
class ChatMessage {
  Id id = Isar.autoIncrement;

  late int windowId;

  late String role;

  late String modelId;

  late String modelName;

  late String content;

  late String status;

  String? messageId;

  String? groupId;

  String? conversationUrl;

  late DateTime createdAt;

  late DateTime updatedAt;
}