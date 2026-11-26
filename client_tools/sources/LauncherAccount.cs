using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace OfSfPlayer {
    using Data = Dictionary<string,object>;

    internal sealed partial class PlayerWindow {
        static string Error(Data response) {
            string message=Get(response,"message");
            if(message!="")return message;
            switch(Get(response,"code")){
                case "captcha_invalid":return "验证码错误或已过期，请换一张重试。";
                case "captcha_required":return "请输入图片验证码。";
                case "invalid_credentials":return "账号或密码错误。";
                case "username_taken":return "账号已注册，可切换到登录。";
                case "nickname_taken":return "角色名已被使用，请另选一个。";
                case "registration_closed":return "服主已关闭注册。";
                case "invalid_username":case "invalid_account":return "账号须为 3–40 位汉字、英文字母、数字、下划线、点或短横线。";
                case "password_unchanged":return "新密码须与当前密码不同。";
                case "invalid_password":return "密码长度须为 8–128 个字符。";
                case "rate_limited":return "操作频繁，请稍后再试。";
                default:return Get(response,"status")=="429"?"请求过于频繁，请稍后再试。":"区服未完成请求（"+Get(response,"code")+"）。";
            }
        }
        bool ConfirmFingerprint(string endpoint,string digest){
            string upper=digest.ToUpperInvariant();
            var box=new Window{Title="核对区服证书",Owner=this,SizeToContent=SizeToContent.Height,Width=480,ResizeMode=ResizeMode.NoResize,
                WindowStartupLocation=WindowStartupLocation.CenterOwner,FontFamily=FontFamily,Background=Brush("#FBFCF9")};
            var body=new StackPanel{Margin=new Thickness(22)};box.Content=body;
            body.Children.Add(new TextBlock{Text=endpoint,FontWeight=FontWeights.SemiBold,FontSize=17,TextWrapping=TextWrapping.Wrap});
            body.Children.Add(new TextBlock{Text="此区服使用独立证书。请通过另一渠道向服主核对完整 SHA-256 指纹。",TextWrapping=TextWrapping.Wrap,Margin=new Thickness(0,10,0,8)});
            var value=new TextBox{Text=String.Join("  ",Enumerable.Range(0,8).Select(i=>upper.Substring(i*8,8))),FontFamily=new FontFamily("Consolas"),TextWrapping=TextWrapping.Wrap,IsReadOnly=true,Padding=new Thickness(8)};
            body.Children.Add(value);
            body.Children.Add(new TextBlock{Text="核对一致后，输入服主提供的指纹末 8 位。",Margin=new Thickness(0,12,0,5)});
            var confirm=new TextBox{MaxLength=8,Padding=new Thickness(7)};body.Children.Add(confirm);
            var buttons=new StackPanel{Orientation=Orientation.Horizontal,HorizontalAlignment=HorizontalAlignment.Right,Margin=new Thickness(0,14,0,0)};body.Children.Add(buttons);
            var cancel=new Button{Content="取消",IsCancel=true,Padding=new Thickness(14,6,14,6)};buttons.Children.Add(cancel);
            var accept=new Button{Content="核对一致并保存",IsEnabled=false,Margin=new Thickness(7,0,0,0),Padding=new Thickness(12,6,12,6)};buttons.Children.Add(accept);
            confirm.TextChanged+=delegate {accept.IsEnabled=String.Equals(confirm.Text.Trim(),upper.Substring(56),StringComparison.OrdinalIgnoreCase);};
            accept.Click+=delegate{if(accept.IsEnabled)box.DialogResult=true;};
            return box.ShowDialog()==true;
        }
        async Task EnsureConnection(bool force,bool renew) {
            if(Address=="")throw new IOException("请填写服主提供的服务器 IP 或域名。");
            if(!force&&connection!=null&&verifiedInput==Address)return;
            var input=new Data{{"address",Address},{"caFile",renew?"":caOverride},{"renew",renew}};
            Status("正在验证区服证书和账号服务…");
            var result=await Json("player_connection.py",input);
            if(Get(result,"code")=="fingerprint_required"){
                string digest=Get(result,"fingerprint");
                if(digest.Length!=64||!ConfirmFingerprint(Get(result,"address"),digest))throw new IOException("已取消连接。");
                input["fingerprint"]=digest; result=await Json("player_connection.py",input);
                if(Flag(result,"ok")&&C<CheckBox>("Remember").IsChecked==true){
                    C<CheckBox>("Remember").IsChecked=false;
                    C<PasswordBox>("Secret").Clear();
                    ResetIdentity();
                }
            }
            if(!Flag(result,"ok"))throw new IOException(Error(result));
            var box=C<ComboBox>("Endpoint");string display=DisplayAddress(Get(result,"publicTls"));
            if(!box.Items.Contains(display))box.Items.Add(display);
            normalizingEndpoint=true;
            try{if(box.Text!=display)box.Text=display;}finally{normalizingEndpoint=false;}
            verifiedInput=Address;caOverride=Get(result,"caFile");connection=result;
        }
        Data Config(){
            var mode=(ComboBoxItem)C<ComboBox>("DisplayMode").SelectedItem;
            return new Data{{"source",TextOf("Source")},{"base",TextOf("Work")},{"publicTls",Get(connection,"publicTls")},
                            {"caFile",Get(connection,"caFile")},{"display",(string)mode.Tag}};
        }
        async Task<Data> Api(string action,Data data){
            data["server"]=Config();data["action"]=action;
            try{return await Json("private_account_api.py",data);}finally{data.Clear();}
        }
        async Task NewChallenge(string type,string next){
            var doc=await Api("challenge",new Data{{"username",TextOf("Username")},{"purpose",type}});
            if(!Flag(doc,"ok"))throw new IOException(Error(doc));
            using(var image=new MemoryStream(Convert.FromBase64String(Get(doc,"image_png"))))C<Image>("CaptchaImage").Source=Bitmap(image);
            challenge=Get(doc,"challenge_id");purpose=type;pending=next;C<TextBox>("Answer").Clear();C<StackPanel>("CaptchaPanel").Visibility=Visibility.Visible;
            Caption();Status("请填写图片中的验证码后继续。");
        }
        async Task<bool> Auth(string action) {
            bool importing=action=="import";
            if(session!=null&&C<PasswordBox>("Secret").Password.Length==0&&!registering&&DateTime.UtcNow<(importing?importUntil:playUntil))return true;
            bool create=action=="register"||(importing&&registering);
            string type=create?"register":"login";
            var form=new Data{{"username",TextOf("Username")},{"password",C<PasswordBox>("Secret").Password},{"nickname",create?TextOf("Nickname"):""}};
            if(Get(form,"password")=="")throw new IOException("请输入本区服的密码。");
            if(proof!="")form["account_proof"]=proof;
            if(challenge!=""&&purpose==type){
                if(TextOf("Answer")==""){Status("请先填写图片验证码。");return false;}
                form["challenge_id"]=challenge; form["answer"]=TextOf("Answer");
            }
            Status(create?"正在创建区服账号…":"正在验证区服账号…");
            var receipt=await Api(type,form);proof="";
            if(!Flag(receipt,"ok")){
                string code=Get(receipt,"code");
                if(code=="captcha_required"||code=="captcha_invalid"){
                    await NewChallenge(type,action);
                    if(code=="captcha_invalid")Status("验证码错误或已过期。请填写新图片中的字符。");
                    return false;
                }
                ClearChallenge();throw new IOException(Error(receipt));
            }
            try{RememberSuccess(C<PasswordBox>("Secret").Password);}
            catch(Exception){Status("账号已验证。Windows 无法保存本次账号设置。");}
            C<PasswordBox>("Secret").Clear();session=receipt;
            int ttl;Int32.TryParse(Get(receipt,"expires_in"),out ttl);playUntil=DateTime.UtcNow.AddSeconds(Math.Min(Math.Max(0,ttl),86400));
            Int32.TryParse(Get(receipt,"import_expires_in"),out ttl);importUntil=DateTime.UtcNow.AddSeconds(Math.Min(Math.Max(0,ttl),900));
            ClearChallenge();SetMode(false,true);return true;
        }
        async Task UpdatePassword(){
            if(!Flag(connection,"passwordChange"))throw new IOException("此区服尚未支持玩家修改密码，请联系管理员。");
            string next=C<PasswordBox>("NewPassword").Password;
            string old=C<PasswordBox>("Secret").Password;
            if(old.Length==0)throw new IOException("请输入当前密码。");
            int length=next.Count(c=>!Char.IsLowSurrogate(c));
            if(length<8||length>128)throw new IOException("新密码长度须为 8–128 个字符。");
            if(next==old)throw new IOException("新密码须与当前密码不同。");
            if(next!=C<PasswordBox>("ConfirmPassword").Password)throw new IOException("两次输入的新密码不一致。");
            var data=new Data{{"username",TextOf("Username")},{"password",old},{"new_password",next}};
            if(challenge!=""&&purpose=="change-password"){
                if(TextOf("Answer")==""){Status("请填写验证码。");return;}
                data["challenge_id"]=challenge;data["answer"]=TextOf("Answer");
            }
            var reply=await Api("change-password",data);
            if(!Flag(reply,"ok")){
                if(Get(reply,"code")=="captcha_required"||Get(reply,"code")=="captcha_invalid"){
                    await NewChallenge("change-password","change-password");return;
                }
                throw new IOException(Error(reply));
            }
            C<PasswordBox>("Secret").Clear();SetPasswordMode(false);ResetIdentity();
            try{RememberSuccess(next);}catch(Exception){
                Status("密码已修改，请用新密码登录。Windows 本机账号记录更新失败。");return;
            }
            if(C<CheckBox>("Remember").IsChecked==true)C<PasswordBox>("Secret").Password=next;
            Status("密码已修改，请用新密码重新登录。");
        }
        async void Run(string action) {
            if(busy)return;
            busy=true;view.IsEnabled=false;
            try{
                if(action=="inspect"){
                    Status("正在检查客户端版本和安装文件…");
                    Status((await Execute("player_patch.py","inspect",new Data{{"source",TextOf("Source")}},false)).Trim());
                    return;
                }
                if(action=="uninstall"){
                    if(MessageBox.Show(this,"清除本登陆器在此机器创建的游戏副本和连接设置？远程角色保存在服主服务器。","清除本机副本",MessageBoxButton.YesNo,MessageBoxImage.Question,MessageBoxResult.No)!=MessageBoxResult.Yes)return;
                    await Execute("player_patch.py","uninstall",new Data(),false);
                    C<ComboBox>("Endpoint").Items.Clear();C<ComboBox>("Endpoint").Text="";C<TextBox>("Work").Clear();C<PasswordBox>("Secret").Clear();InvalidateConnection();Status("已清除本机副本。");return;
                }
                if(action=="renew"&&MessageBox.Show(this,"重新检查此地址的证书？如服主已更换私有证书，请先准备新的 SHA-256 指纹。","重新核对",MessageBoxButton.OKCancel,MessageBoxImage.Question,MessageBoxResult.Cancel)!=MessageBoxResult.OK)return;
                if(action=="save"&&Address==""){ShowSettings(false);Status("请先填写服务器地址，再保存连接及目录。");return;}
                await EnsureConnection(action=="test"||action=="renew",action=="renew");
                if(action=="test"||action=="renew"){Status("连接成功，已验证此区服的证书和账号服务。");ShowSettings(true);return;}
                if(action=="save"){await Execute("player_patch.py","save",Config(),false);ShowSettings(false);Status("连接和目录已保存。");return;}
                if(TextOf("Username")=="")throw new IOException("请输入区服账号。");
                if(action=="change-password"){await UpdatePassword();return;}
                if(action=="refresh"){if(pending!="")await NewChallenge(purpose,pending);return;}
                if(action=="check"){
                    if(!Flag(connection,"lookup")){Status("此版本的区服不支持检查账号，可直接登录。");return;}
                    if(challenge==""||purpose!="lookup"){await NewChallenge("lookup","check");return;}
                    if(TextOf("Answer")==""){Status("请输入图片验证码。");return;}
                    var result=await Api("lookup",new Data{{"username",TextOf("Username")},{"challenge_id",challenge},{"answer",TextOf("Answer")}});
                    if(!Flag(result,"ok")){
                        if(Get(result,"code")=="captcha_invalid"||Get(result,"code")=="captcha_required"){await NewChallenge("lookup","check");Status("请填写新图片中的验证码。");return;}
                        throw new IOException(Error(result));
                    }
                    bool exists=Flag(result,"exists");ClearChallenge();SetMode(!exists,true);proof=Get(result,"account_proof");
                    Status(exists?"账号已注册，输入密码即可登录。":"账号可用，设置密码后即可注册。");return;
                }
                if(!await Auth(action))return;
                if(action=="register"){Status("账号已创建。可先继承已有存档，或直接进入游戏。");return;}
                if(action=="import"){
                    string token=Get(session,"import_ticket");
                    if(token=="")throw new IOException("所选区服尚不支持存档继承。");
                    var server=Config();using(var dialog=new OfSfImport.ImportWindow("当前区服",Get(session,"nickname"),token,async (operation,values)=>{
                        var request=new Data(values){{"server",server},{"action",operation}};return await Json("private_account_api.py",request);
                    })){
                        dialog.ShowDialog(new DialogOwner(this)); if(dialog.EffectiveNickname!=null)session["nickname"]=dialog.EffectiveNickname;
                    }
                    Status("已返回登陆器，可继续进入游戏。");return;
                }
                var config=Config();
                if(!Directory.Exists(TextOf("Source"))){ShowSettings(true);throw new IOException("选择游戏已安装的 Windows 客户端目录。");}
                Status("正在检查游戏文件，首次准备可能需要几分钟…");
                await Execute("player_patch.py","prepare",config,false);
                config["account"]=new Data{{"slot",Get(session,"slot")},{"ticket",Get(session,"ticket")}};
                session=null;Status("正在启动原游戏客户端…");
                await Execute("player_patch.py",picker?"relaunch":"launch",config,false);busy=false;Close();
            }
            catch(Exception error){Status(error.Message);}
            finally{busy=false;view.IsEnabled=true;}
        }
    }
}
