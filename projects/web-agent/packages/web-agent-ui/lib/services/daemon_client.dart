// ignore_for_file: use_null_aware_elements

import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';

const kDaemonPort = 19305;

class DaemonStatus {
  const DaemonStatus({
    required this.reachable,
    required this.extensionConnected,
    this.extensionVersion,
    this.pending = 0,
  });

  final bool reachable;
  final bool extensionConnected;
  final String? extensionVersion;
  final int pending;
}

class CommandResult {
  const CommandResult({
    required this.ok,
    this.data,
    this.code,
    this.message,
    this.hint,
  });

  final bool ok;
  final Object? data;
  final String? code;
  final String? message;
  final String? hint;

  bool get failed => !ok;
}

class DaemonClient {
  DaemonClient._(this._dio, this._token);

  final Dio _dio;
  final String _token;

  static Future<DaemonClient> create({int port = kDaemonPort}) async {
    final token = await readControlToken();
    final dio = Dio(
      BaseOptions(
        baseUrl: 'http://127.0.0.1:$port',
        connectTimeout: const Duration(seconds: 3),
        receiveTimeout: const Duration(seconds: 30),
        headers: {
          'content-type': 'application/json',
        },
      ),
    );
    return DaemonClient._(dio, token);
  }

  static Future<String> readControlToken() async {
    try {
      final home = Platform.environment['USERPROFILE'] ??
          Platform.environment['HOME'] ??
          '';
      final file = File('$home\\.web-agent\\control-token');
      if (!await file.exists()) return '';
      final raw = await file.readAsString();
      return raw.trim();
    } catch (_) {
      return '';
    }
  }

  bool get hasToken => _token.isNotEmpty;

  Future<bool> ping() async {
    try {
      final response = await _dio.get<void>('/ping');
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  Future<DaemonStatus> status() async {
    try {
      final response = await _dio.get<Map<String, dynamic>>('/status');
      if (response.statusCode != 200 || response.data == null) {
        return const DaemonStatus(reachable: false, extensionConnected: false);
      }
      final body = response.data!;
      return DaemonStatus(
        reachable: true,
        extensionConnected: body['extensionConnected'] == true,
        extensionVersion: body['extensionVersion'] as String?,
        pending: (body['pending'] as num?)?.toInt() ?? 0,
      );
    } catch (_) {
      return const DaemonStatus(reachable: false, extensionConnected: false);
    }
  }

  Future<CommandResult> command(
    String action,
    Object? payload, {
    int? timeoutMs,
  }) async {
    final body = <String, Object?>{
      'action': action,
      if (payload != null) 'payload': payload,
      if (timeoutMs != null) 'timeoutMs': timeoutMs,
    };
    try {
      final response = await _dio.post<Map<String, dynamic>>(
        '/command',
        data: jsonEncode(body),
        options: Options(
          headers: {
            'x-web-agent': '1',
            'authorization': 'Bearer $_token',
          },
        ),
      );
      final data = response.data;
      return CommandResult(
        ok: data?['ok'] == true,
        data: data?['data'],
        code: (data?['error'] as Map?)?.cast<String, dynamic>()['code'] as String?,
        message: (data?['error'] as Map?)?.cast<String, dynamic>()['message'] as String?,
        hint: (data?['error'] as Map?)?.cast<String, dynamic>()['hint'] as String?,
      );
    } on DioException catch (error) {
      final data = error.response?.data;
      if (data is Map<String, dynamic>) {
        return CommandResult(
          ok: data['ok'] == true,
          code: (data['error'] as Map?)?.cast<String, dynamic>()['code'] as String?,
          message: (data['error'] as Map?)?.cast<String, dynamic>()['message'] as String?,
          hint: (data['error'] as Map?)?.cast<String, dynamic>()['hint'] as String?,
        );
      }
      return CommandResult(
        ok: false,
        code: 'http_error',
        message: '无法连接本地守护进程',
        hint: '请先运行 tools/start-ui.ps1 或 web-agent daemon start',
      );
    }
  }
}