# 目录结构约定

不同语言/框架的标准目录布局。进行物理重构时参考此文件。

## Python

```
project/
├── src/                    # 或 <package_name>/
│   ├── __init__.py
│   ├── core/               # 核心逻辑
│   ├── utils/              # 工具函数
│   └── cli.py              # CLI 入口
├── tests/
│   ├── test_core.py
│   └── conftest.py         # pytest fixtures
├── docs/                   # 文档
├── scripts/                # 辅助脚本
├── pyproject.toml          # 项目元数据
├── requirements.txt        # 依赖
├── README.md
└── .gitignore
```

## JavaScript/TypeScript

```
project/
├── src/
│   ├── index.ts            # 入口
│   ├── components/
│   ├── utils/
│   └── types/
├── tests/
│   └── *.test.ts
├── public/                 # 静态资源
├── package.json
├── tsconfig.json
├── README.md
└── .gitignore
```

## Go

```
project/
├── cmd/
│   └── <binary_name>/
│       └── main.go
├── internal/               # 私有包
├── pkg/                    # 可导出库
├── go.mod
├── go.sum
├── README.md
└── .gitignore
```

## Rust

```
project/
├── src/
│   ├── main.rs             # 二进制入口
│   ├── lib.rs              # 库入口
│   └── bin/                # 多二进制
├── tests/
│   └── integration_test.rs
├── Cargo.toml
├── README.md
└── .gitignore
```

## 通用原则

- **源码与测试分离**：源码放 `src/`（或语言惯例目录），测试放 `tests/`
- **文档独立**：文档放 `docs/`，不散落在根目录
- **示例独立**：示例代码放 `examples/`
- **根目录整洁**：根目录只留配置文件 + README + .gitignore
- **隐藏文件/目录**：`.github/`、`.vscode/` 等工具配置不以业务文件混放
