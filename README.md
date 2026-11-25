# OF_SF 登陆器

Windows 10/11 x64 原生 WPF 登陆器。使用已安装的 TapTap PC 1.1.12，通过独立账号连接服主的区服。支持 11 月 7 日和 11 月 25 日资源清单。

[下载 Windows x64 登陆器（EXE）](https://github.com/YCLiuge/OF_SF_Launcher/releases/latest/download/OF_SF-Launcher-Windows-x64.exe) · [便携 ZIP 和 SHA-256 校验文件](https://github.com/YCLiuge/OF_SF_Launcher/releases/latest)

`v1.1.12-r3` 支持汉字账号名，继承备份时会核对游戏内显示的 UID 和角色名。短暂掉线后，同一游戏进程可在两分钟内重连原角色。请同时更新区服与登陆器。Windows 文件版本为 `1.1.12.3`。

## 开始游玩

双击 `OF_SF 登陆器.exe`，首次运行会在旁边的同名目录准备文件。升级时把 EXE 放到原版登陆器文件夹的上一级，可直接沿用连接、工作目录和已准备的游戏。也可以将 `OF_SF-Launcher-1.1.12-Windows-x64.zip` 完整解压并启动里面的 EXE。填写服主提供的 IP、域名或 HTTPS 地址，可指定端口，默认 **18443**。

已有账号选择「登录」，新玩家点「注册」。「检查账号」通过区服的图片验证码后提示下一步。系统信任的证书直接验证，原来保存的连接和私有 CA 可以沿用。首次遇到私有证书时，请通过另一渠道与服主核对完整 SHA-256 指纹，再输入末 8 位确认。

有全进度备份的玩家，请在首次进入游戏前点「继承已有存档」，预览 UID、角色名和可恢复内容，再确认。原游戏和本机区服副本各自保存设置；退出后可直接从 TapTap 玩官服。[玩家指南](client_tools/PLAYER_PATCH.md)

## 构建

需要 Windows x64、Python 3.11+、.NET SDK **9.0.306**、.NET Framework 4.8 Developer Pack，以及配套服务端仓库中的运行时和国服资源。使用本地提供的 `poster112_绿荫小憩.png`，构建脚本会核验原画摘要。服务端资源和依赖的导入步骤见 [OF_SF 构建文档](https://github.com/YCLiuge/OF_SF/blob/main/tools/BUILD.md)。

构建 `v1.1.12-r3` 请检出配套服务端版本。[OF_SF c91560c](https://github.com/YCLiuge/OF_SF/commit/c91560c27247aed61094bb66258e8c05cd15a1a9)

```powershell
py -3 tools/package_player_patch.py --server-checkout ../OF_SF --artwork "素材目录/poster112_绿荫小憩.png" --output dist/OF_SF-Launcher-1.1.12-Windows-x64.zip
py -3 tools/test_player_patch.py --server-checkout ../OF_SF --archive dist/OF_SF-Launcher-1.1.12-Windows-x64.zip
```

输出包括单文件 EXE、可移植 ZIP 和各自的 SHA-256。回归需要 `openssl` 可执行文件，使用临时区服、测试账号和合成备份，覆盖证书、注册登录、WPF 控件、存档导入以及原生玩法协议。

本仓库维护玩家界面、连接设置、便携安装和验收工具。账号协议、游戏副本准备、存档预览、游戏数据和服务器运行时由 [OF_SF](https://github.com/YCLiuge/OF_SF) 提供。普通玩家运行发行包即可。

本项目仅供技术学习与交流。使用者应遵守有关法律法规，对自身使用行为负责。相关素材的权利归原权利人所有；如有侵权，请联系维护者删除。
