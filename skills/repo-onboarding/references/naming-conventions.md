# 命名约定

代码仓库中文件和目录的命名规范参考。

## 通用规则

| 规范 | 示例 | 适用场景 |
|------|------|---------|
| `snake_case` | `user_service.py`, `data_loader.go` | Python, Rust, Go, SQL |
| `kebab-case` | `user-service.ts`, `my-component.jsx` | 文件名、目录名、CSS |
| `camelCase` | `userService.js`, `getUserName()` | JavaScript, Java 方法 |
| `PascalCase` | `UserService.tsx`, `UserModel.java` | 类名、React 组件 |

## 按语言

### Python
- 模块/包：`snake_case`（如 `agent_loop.py`、`tools/`）
- 类：`PascalCase`（如 `ToolRegistry`、`DeepSeekBackend`）
- 函数/方法：`snake_case`（如 `build_default_registry()`）
- 常量：`UPPER_SNAKE_CASE`（如 `SYSTEM_PROMPT`）
- 测试文件：`test_<module>.py`

### JavaScript/TypeScript
- 文件名：`kebab-case` 或 `camelCase`
- 组件文件：`PascalCase`（如 `UserProfile.tsx`）
- 函数：`camelCase`（如 `getUserData()`）
- 常量：`UPPER_SNAKE_CASE` 或 `camelCase`

### Go
- 包：全小写，单字优先（如 `http`、`json`）
- 导出符号：`PascalCase`（如 `NewServer`）
- 非导出符号：`camelCase`（如 `parseRequest`）
- 文件名：`snake_case`（如 `user_service.go`）

### Rust
- 模块/文件：`snake_case`（如 `user_service.rs`）
- 类型/Trait：`PascalCase`（如 `HttpClient`）
- 函数：`snake_case`（如 `handle_request()`）
- 常量：`UPPER_SNAKE_CASE`

## 反模式（应避免）

| 反模式 | 原因 |
|--------|------|
| 中文文件名 | 跨平台兼容性差，终端中可能乱码 |
| 文件名含空格 | shell 操作需额外转义 |
| 无意义命名（`test1.py`, `tmp.py`, `a.txt`） | 无法从文件名推断内容 |
| 版本号后缀（`report_v2_final.py`） | 用 git 管理版本 |
| 过长文件名（>50 字符） | 不便阅读和操作 |
