# OF_SF 登陆器

Windows 10/11 x64 原生 WPF 登陆器。使用已安装的 TapTap PC 1.1.12，通过独立账号连接服主的区服。已实测国服 11 月 7 日、11 月 25 日两个批次的资源。

[下载 Windows x64 登陆器（EXE）](https://github.com/YCLiuge/OF_SF_Launcher/releases/latest/download/OF_SF-Launcher-Windows-x64.exe) · [便携 ZIP 和 SHA-256 校验文件](https://github.com/YCLiuge/OF_SF_Launcher/releases/latest)

输入服务器地址后，登陆器会在停顿 0.8 秒后验证区服并显示响应时间（含 TLS 握手）。手动「设置 → 连接与证书 → 测试区服连接」还能检查统一协议目录。旧版区服仍可登录。[OF_Contracts](https://github.com/YCLiuge/OF_Contracts)

「设置 → Mod 扩展」可为私服游戏副本安装上游宿主和独立区服目录，提供在线下载和本地 ZIP 两种方式。上游持续独立维护。首次使用需要在副本中安装 MelonLoader。[安装步骤](client_tools/PLAYER_PATCH.md#mod-扩展) · [上游项目](https://github.com/jianx14/overfiled-mod)

在「设置 → 游戏显示模式」中提供「跟随游戏设置」「无边框全屏」和 1280 × 720 窗口模式，保存后下次启动生效。遇到全屏黑屏时可选择窗口模式恢复。设置页的「检查客户端版本」可以离线核对渠道和版本。

## 开始游玩

首次热更时，r6 起的登陆器和配套区服会为已启动的游戏进程保留一小时。r5 公网区服保留五分钟。超过时限请退出游戏，在登陆器重新登录。

双击 `OF_SF 登陆器.exe`，首次运行会在旁边的同名目录准备文件。升级时把 EXE 放到原版登陆器文件夹的上一级，可直接沿用连接、工作目录和已准备的游戏。也可以将 `OF_SF-Launcher-1.1.12-Windows-x64.zip` 完整解压并启动里面的 EXE。填写服主提供的 IP、域名或 HTTPS 地址，可指定端口，默认 **18443**。

已有账号选择「登录」，新玩家点「注册」。「检查账号」通过区服的图片验证码后提示下一步。设置中可以选择游戏安装目录、渠道启动器目录或 TapTap、Steam 游戏库。登陆器会核对清单及程序指纹。已核对 B站国服 1.1.12.0、Steam 国际版 Build 26178963 的发行文件及各自 SDK。TapTap 国服已验证进图；B站及 Steam 的区服登录仍待验证。游戏内出现的 NEKOTAN 或渠道登录窗由发行方客户端管理。区服账号仅在 OF_SF 登陆器输入。[发行方 PC 登录说明](https://www.taptap.cn/moment/656591245195346144)

系统信任的证书直接验证。系统根证书尚未更新时，登陆器也可验证链至 Sectigo R46 的公开 TLS 证书，原来保存的连接和私有 CA 可以沿用。首次遇到私有证书时，请通过另一渠道与服主核对完整 SHA-256 指纹，再输入末 8 位确认。[Sectigo 公有根证书](https://www.sectigo.com/faqs/detail/Sectigo-Public-Intermediates-and-Roots)

有全进度备份的玩家，请在首次进入游戏前点「继承已有存档」，预览 UID、角色名和可恢复内容，再确认。备份中可核实的个人签名也可恢复。原游戏和本机区服副本各自保存设置；退出后可直接从 TapTap 玩官服。[玩家指南](client_tools/PLAYER_PATCH.md)

## 构建

需要 Windows x64、Python 3.11+、.NET SDK **9.0.306**、.NET Framework 4.8 Developer Pack，以及配套服务端仓库中的运行时和国服资源。使用本地提供的 `poster112_绿荫小憩.png`，构建脚本会核验原画摘要。服务端资源和依赖的导入步骤见 [OF_SF 构建文档](https://github.com/YCLiuge/OF_SF/blob/main/tools/BUILD.md)。

构建本版请检出配套服务端版本。[OF_SF 965d5be](https://github.com/YCLiuge/OF_SF/commit/965d5be0140cc630fa952df58953e29aa40d60d8)

```powershell
py -3 tools/package_player_patch.py --server-checkout ../OF_SF --artwork "素材目录/poster112_绿荫小憩.png" --output dist/OF_SF-Launcher-1.1.12-Windows-x64.zip
py -3 tools/test_player_mod.py
py -3 tools/test_player_patch.py --server-checkout ../OF_SF --archive dist/OF_SF-Launcher-1.1.12-Windows-x64.zip
```

输出包括单文件 EXE、可移植 ZIP 和各自的 SHA-256。回归需要 `openssl` 可执行文件，使用临时区服、测试账号和合成备份，覆盖证书、注册登录、WPF 控件、存档导入以及原生玩法协议。

本仓库维护玩家界面、连接设置、便携安装和验收工具。账号协议、游戏副本准备、存档预览、游戏数据和服务器运行时由 [OF_SF](https://github.com/YCLiuge/OF_SF) 提供。普通玩家运行发行包即可。

本项目仅供技术学习与交流。使用者应遵守有关法律法规，对自身使用行为负责。相关素材的权利归原权利人所有；如有侵权，请联系维护者删除。
