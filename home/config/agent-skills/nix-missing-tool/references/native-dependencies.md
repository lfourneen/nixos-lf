# 编译与运行时依赖

根据错误发生在编译、链接还是程序启动阶段选择处理方法。

## 编译缺头文件或链接库

将编译工具和库放入同一开发环境。以 zlib 为例：

```bash
nix-shell -p pkg-config zlib --run \
  'cc main.c $(pkg-config --cflags --libs zlib) -o app'
```

库包名、pkg-config 模块名和头文件名可能不同，先查包及其输出。手写 derivation 时按构建角色使用 `nativeBuildInputs` 和 `buildInputs`；交叉编译还需区分构建端工具与目标端库。

## 预编译程序无法启动

先用 `file` 确认架构和文件类型。脚本检查 shebang；ELF 程序用 `patchelf --print-interpreter`、`patchelf --print-needed` 和 `patchelf --print-rpath` 检查加载器与依赖。

本机提供 nix-ld，系统库目录为 `/run/current-system/sw/share/nix-ld/lib`。它用于使用系统 ELF 加载器路径的外来二进制；当前环境可直接检查：

```bash
printf 'NIX_LD=%s\nNIX_LD_LIBRARY_PATH=%s\n' "${NIX_LD:-}" "${NIX_LD_LIBRARY_PATH:-}"
ls -ld -- /run/current-system/sw/share/nix-ld/lib
```

将 `patchelf --print-interpreter` 输出的绝对路径作为目标，用 `ls -l -- 加载器路径` 检查它是否存在。路径存在且指向 nix-ld 时使用现有支持；缺库时为该进程补充 `NIX_LD_LIBRARY_PATH`。系统库目录或加载器缺失时，记录当前系统、架构和缺失路径，再选择适用的运行环境。

下面是临时 `.nix` 文件的示例，按实际缺失库调整并保存到 `/tmp/runtime-libs.nix`：

```nix
let pkgs = import <nixpkgs> { };
in pkgs.mkShell {
  NIX_LD = pkgs.stdenv.cc.bintools.dynamicLinker;
  NIX_LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.zlib ]
    + ":/run/current-system/sw/share/nix-ld/lib";
}
```

用 `nix-shell /tmp/runtime-libs.nix --run '/程序的绝对路径'` 调用，与当前目录无关。示例保留了上述系统库目录；目录不存在的环境需去掉该条目并提供实际需要的库。临时表达式无需转成项目 Flake。

Nix 打包的 Python、Node 等使用 store 中的加载器，不通过 nix-ld，因而不会自动采用 `NIX_LD_LIBRARY_PATH`。这类进程加载额外 `.so` 时优先补齐对应 Nix 包；确需 `LD_LIBRARY_PATH` 时，只在该进程范围加入所需库目录。

需要持久打包外来程序时，再选择 `autoPatchelfHook`、wrapper 或 FHS 环境。临时排障保留原二进制，先验证库路径方案。

官方说明：[nix-ld 用法与解释器差异](https://github.com/nix-community/nix-ld#usage)。
