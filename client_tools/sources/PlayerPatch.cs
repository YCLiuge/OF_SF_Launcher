using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Forms = System.Windows.Forms;

namespace OfSfPlayer {
    using Data = Dictionary<string,object>;

    internal sealed partial class PlayerWindow : Window {
        readonly string root = AppDomain.CurrentDomain.BaseDirectory;
        readonly bool picker, reconnect, demo;
        readonly FrameworkElement view;
        bool busy, registering, inSettings, normalizingEndpoint;
        string caOverride="", verifiedInput="", proof="", challenge="", purpose="", pending="";
        Data connection, session;
        readonly RealmAccounts accounts;
        bool filling, changingPassword;
        string shownRealm="",filledUser="";
        DateTime playUntil, importUntil;

        internal PlayerWindow(bool selectRealm, bool reconnecting, bool demonstration)
            : this(selectRealm,reconnecting,demonstration,@"Software\OF_SF\RealmAccounts") {}
        internal PlayerWindow(bool selectRealm, bool reconnecting, bool demonstration, string memoryBranch) {
            accounts=new RealmAccounts(memoryBranch);
            picker=selectRealm; reconnect=reconnecting; demo=demonstration;
            Title="登陆器"; Width=1000; Height=646; MinWidth=780; MinHeight=540;
            WindowStartupLocation=WindowStartupLocation.CenterScreen;
            var assembly=Assembly.GetExecutingAssembly();
            using(var input=assembly.GetManifestResourceStream("LauncherWindow.xaml"))view=(FrameworkElement)XamlReader.Load(input);
            Content=view;
            using(var input=assembly.GetManifestResourceStream("Illustration.png")){
                var bitmap=Bitmap(input);
                var crop=new CroppedBitmap(bitmap,new Int32Rect(0,300,2048,1452));crop.Freeze();
                C<Image>("Artwork").Source=crop;
            }
            Click("LoginTab",()=>{SetPasswordMode(false);SetMode(false,false);}); Click("RegisterTab",()=>{SetPasswordMode(false);SetMode(true,false);});
            Click("Options",()=>ShowSettings(!inSettings)); Click("AcceptSettings",()=>Run("save"));
            Click("InspectSource",()=>Run("inspect")); Click("SourceBrowse",()=>Browse("Source")); Click("BaseBrowse",()=>Browse("Work"));
            Click("SelectCa",SelectCa); Click("Test",()=>Run("test")); Click("Reverify",()=>Run("renew"));
            Click("ClearWork",()=>Run("uninstall")); Click("CheckAccount",()=>Run("check"));
            Click("RefreshCaptcha",()=>Run("refresh")); Click("ImportSave",()=>Run("import"));
            Click("Primary",()=>Run(changingPassword?"change-password":pending!=""?pending:registering?"register":"play"));
            Click("Forgot",()=>Status("请联系管理员修改密码"));
            Click("ChangePassword",()=>{SetMode(false,true);SetPasswordMode(!changingPassword);});
            C<CheckBox>("Remember").Unchecked+=delegate{
                if(!filling){
                    try{
                        var saved=accounts.Read(shownRealm);
                        accounts.ForgetPassword(shownRealm);
                        if(saved[0]==TextOf("Username")&&saved[1]!=""&&saved[1]==C<PasswordBox>("Secret").Password)
                            C<PasswordBox>("Secret").Clear();
                    }catch(Exception){Status("无法清除 Windows 中的已保存密码。");}
                }
            };
            C<TextBox>("Username").TextChanged+=delegate {
                ResetIdentity();
                if(!filling&&filledUser!=""&&filledUser!=TextOf("Username")){
                    filling=true;
                    try{C<PasswordBox>("Secret").Clear();C<CheckBox>("Remember").IsChecked=false;filledUser="";}
                    finally{filling=false;}
                }
            };
            C<ComboBox>("Endpoint").AddHandler(TextBox.TextChangedEvent,new TextChangedEventHandler(delegate(object sender,TextChangedEventArgs args){
                if(!normalizingEndpoint){
                    caOverride="";InvalidateConnection();
                    var edit=args.OriginalSource as TextBox;
                    FillAccount(RealmAccounts.Endpoint(edit==null?Address:edit.Text));
                }
            }));
            Closing+=delegate(object s,System.ComponentModel.CancelEventArgs e){if(busy)e.Cancel=true;};
            SetMode(false,true); if(!demo){Restore();FillAccount(RealmAccounts.Endpoint(Address));}
            if(reconnect) Status("登录已过期或连接已中断。请重新登录当前区服以返回游戏。");
        }
        T C<T>(string key) where T:FrameworkElement{return (T)view.FindName(key);}
        void Click(string name, Action handler){C<Button>(name).Click+=delegate{handler();};}
        static string Get(Data doc,string field){object v;return doc!=null&&doc.TryGetValue(field,out v)&&v!=null?Convert.ToString(v):"";}
        static bool Flag(Data doc,string field){object v;return doc!=null&&doc.TryGetValue(field,out v)&&v is bool&&(bool)v;}
        static BitmapSource Bitmap(Stream input){
            var image=new BitmapImage();image.BeginInit();image.CacheOption=BitmapCacheOption.OnLoad;
            image.StreamSource=input;image.EndInit();image.Freeze();return image;
        }
        void Status(string value){C<TextBlock>("Status").Text=value;}
        string TextOf(string id){return C<TextBox>(id).Text.Trim();}
        string Address { get { return C<ComboBox>("Endpoint").Text.Trim(); } }
        void ShowSettings(bool show){
            inSettings=show; C<StackPanel>("SettingsPanel").Visibility=show?Visibility.Visible:Visibility.Collapsed;
            C<StackPanel>("AccountPanel").Visibility=show?Visibility.Collapsed:Visibility.Visible;
            C<Button>("Options").Content=show?"返回":"设置";
            C<TextBlock>("TrustLabel").Text=Flag(connection,"bundledPublicCa")?"已通过登陆器内置的公有根证书验证。":caOverride!=""?"已选择本地 CA 文件":connection!=null&&Get(connection,"caFile")!=""?"此区服使用已核对的专用证书。":"优先使用系统及已保存的可信证书。";
        }
        void SetMode(bool reg,bool keep){
            if(!keep&&registering!=reg)ResetIdentity();
            if(!keep&&registering!=reg){
                C<PasswordBox>("Secret").Clear();
                if(reg){
                    filling=true;try{C<CheckBox>("Remember").IsChecked=false;}finally{filling=false;}
                    C<TextBox>("Username").Clear();
                }else {registering=false;FillAccount(shownRealm,true);}
            }
            registering=reg;
            C<StackPanel>("NamePanel").Visibility=reg?Visibility.Visible:Visibility.Collapsed;
            C<TextBlock>("Heading").Text=reg?"创建账号":reconnect?"重新登录":"账号登录";
            foreach(var tab in new[]{"LoginTab","RegisterTab"}){
                bool selected=(tab=="RegisterTab")==reg;
                var button=C<Button>(tab);button.Background=Brush(selected?"#D0E6D9":"#EDF2ED");button.Foreground=Brush(selected?"#174B3A":"#526359");
            }
            Caption();
        }
        void FillAccount(string realm,bool reload=false){
            if(!reload&&realm==shownRealm)return;
            filling=true;
            try{
                shownRealm=realm;filledUser="";
                C<TextBox>("Username").Clear();C<PasswordBox>("Secret").Clear();
                C<PasswordBox>("NewPassword").Clear();C<PasswordBox>("ConfirmPassword").Clear();
                C<CheckBox>("Remember").IsChecked=false;
                string[] saved=accounts.Read(realm);
                C<TextBox>("Username").Text=saved[0];filledUser=saved[0];
                if(!registering&&saved[1]!=""){C<PasswordBox>("Secret").Password=saved[1];C<CheckBox>("Remember").IsChecked=true;}
            }catch(Exception){Status("无法读取 Windows 中的区服账号，请手动输入。");}
            finally{filling=false;}
        }
        void SetPasswordMode(bool enabled){
            if(changingPassword==enabled)return;
            changingPassword=enabled;ResetIdentity();SetMode(false,true);
            C<PasswordBox>("NewPassword").Clear();C<PasswordBox>("ConfirmPassword").Clear();
            C<StackPanel>("NewPasswordPanel").Visibility=enabled?Visibility.Visible:Visibility.Collapsed;
            C<TextBlock>("PasswordLabel").Text=enabled?"当前密码":"密码";
            C<Button>("ChangePassword").Content=enabled?"返回登录":"修改密码";
            C<Button>("ImportSave").Visibility=enabled?Visibility.Collapsed:Visibility.Visible;
            C<Button>("CheckAccount").Visibility=enabled?Visibility.Collapsed:Visibility.Visible;
            if(enabled)C<TextBlock>("Heading").Text="修改密码";
            Caption();
        }
        void RememberSuccess(string secret){
            accounts.Save(RealmAccounts.Endpoint(DisplayAddress(Get(connection,"publicTls"))),
                          TextOf("Username"),C<CheckBox>("Remember").IsChecked==true?secret:"");
            filledUser=TextOf("Username");
        }
        static Brush Brush(string color){return (Brush)new BrushConverter().ConvertFromString(color);}
        void Caption(){
            C<Button>("Primary").Content=changingPassword?"确认修改密码":pending=="check"?"验证并检查账号":pending=="import"?"验证并打开存档":registering?"注册账号":reconnect?"重新登录并进入游戏":picker?"登录并切换区服":"登录并进入游戏";
        }
        void ClearChallenge(){
            challenge="";purpose="";pending="";C<TextBox>("Answer").Clear();
            C<Image>("CaptchaImage").Source=null;C<StackPanel>("CaptchaPanel").Visibility=Visibility.Collapsed;Caption();
        }
        void ResetIdentity(){session=null;proof="";ClearChallenge();}
        void InvalidateConnection(){connection=null;verifiedInput="";ResetIdentity();}
        static string DisplayAddress(string uri){
            Uri value;
            if(!Uri.TryCreate(uri,UriKind.Absolute,out value)||value.Scheme!="https")return "";
            return value.IsDefaultPort?"https://"+value.Host+":443":uri;
        }
        void Restore(){
            try{
                var file=Path.Combine(root,"player-settings.json");
                Data saved=File.Exists(file)?new JavaScriptSerializer().Deserialize<Data>(File.ReadAllText(file,Encoding.UTF8)):new Data();
                if(!File.Exists(file))saved=LegacyPreset()??saved;
                C<TextBox>("Source").Text=Get(saved,"source");C<TextBox>("Work").Text=Get(saved,"base");
                foreach(ComboBoxItem mode in C<ComboBox>("DisplayMode").Items)
                    if((string)mode.Tag==Get(saved,"display"))C<ComboBox>("DisplayMode").SelectedItem=mode;
                object history;var box=C<ComboBox>("Endpoint");
                if(saved.TryGetValue("servers",out history)&&history is IEnumerable)
                    foreach(object obj in (IEnumerable)history){var address=DisplayAddress(Get(obj as Data,"publicTls"));if(address!=""&&!box.Items.Contains(address))box.Items.Add(address);}
                string current=DisplayAddress(Get(saved,"publicTls"));
                if(current!=""){if(!box.Items.Contains(current))box.Items.Add(current);box.Text=current;caOverride=Get(saved,"caFile");}
            }catch(Exception){Status("无法读取原连接设置，请重新选择区服。");}
            if(TextOf("Source")=="")foreach(var drive in DriveInfo.GetDrives()){
                if(drive.DriveType!=DriveType.Fixed)continue;
                var src=Path.Combine(drive.Name,@"TapTap\PC Games\176228");
                if(File.Exists(Path.Combine(src,"launcher.exe"))){C<TextBox>("Source").Text=src;break;}
            }
        }
        Data LegacyPreset(){
            string index=Path.Combine(root,"player-files.json"),preset=Path.Combine(root,"realm-preset.json"),ca=Path.Combine(root,"realm-ca.pem");
            if(!File.Exists(index)||!File.Exists(preset)||!File.Exists(ca))return null;
            var serial=new JavaScriptSerializer();var files=(Data)serial.Deserialize<Data>(File.ReadAllText(index))["files"];
            using(var algorithm=System.Security.Cryptography.SHA256.Create())foreach(var path in new[]{preset,ca}){
                if(new FileInfo(path).Length>65536)return null;
                using(var input=File.OpenRead(path))if(!String.Equals(BitConverter.ToString(algorithm.ComputeHash(input)).Replace("-",""),Get(files,Path.GetFileName(path)),StringComparison.OrdinalIgnoreCase))return null;
            }
            var entry=serial.Deserialize<Data>(File.ReadAllText(preset));
            return Get(entry,"version")=="1"&&Get(entry,"caFile")=="realm-ca.pem"?new Data{{"publicTls",Get(entry,"publicTls")},{"caFile",ca}}:null;
        }
        void Browse(string id){
            using(var box=new Forms.FolderBrowserDialog()){
                box.Description=id=="Source"?"选择游戏安装目录或渠道启动器目录":"选择私服工作目录";
                if(Directory.Exists(TextOf(id)))box.SelectedPath=TextOf(id);
                if(box.ShowDialog(new DialogOwner(this))==Forms.DialogResult.OK)C<TextBox>(id).Text=box.SelectedPath;
            }
        }
        void SelectCa(){
            var box=new Microsoft.Win32.OpenFileDialog{Title="选择服主提供的 CA 证书",Filter="PEM 证书|*.pem;*.crt",CheckFileExists=true};
            if(box.ShowDialog(this)==true){InvalidateConnection();caOverride=box.FileName;ShowSettings(true);}
        }
        internal sealed class DialogOwner : Forms.IWin32Window {
            public IntPtr Handle {get;private set;}
            internal DialogOwner(Window w){Handle=new WindowInteropHelper(w).Handle;}
        }
        async Task<Data> Json(string module,Data data){
            string text=await Execute(module,"",data,true);
            return new JavaScriptSerializer().Deserialize<Data>(text);
        }
        Task<string> Execute(string module,string action,Data data,bool json){
            return Task.Run(delegate{
                string py=Path.Combine(root,@"OverField-Server\runtime-win64\python.exe"), script=Path.Combine(root,"client_tools",module);
                if(!File.Exists(py)||!File.Exists(script))throw new IOException("请先完整解压登陆器。");
                var psi=new ProcessStartInfo(py,"-B \""+script+"\" "+action){
                    WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,
                    StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
                psi.EnvironmentVariables["PYTHONUTF8"]="1";
                using(var process=Process.Start(psi)){
                    var output=process.StandardOutput.ReadToEndAsync();var error=process.StandardError.ReadToEndAsync();
                    var bytes=Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(data));
                    try{process.StandardInput.BaseStream.Write(bytes,0,bytes.Length);}finally{Array.Clear(bytes,0,bytes.Length);process.StandardInput.Close();}
                    process.WaitForExit();
                    if(process.ExitCode!=0)throw new IOException(json?"账号服务未完成请求。":error.Result.Trim());
                    return output.Result;
                }
            });
        }
        internal void Render(string file){
            UpdateLayout();
            var bitmap=new RenderTargetBitmap((int)view.ActualWidth,(int)view.ActualHeight,96,96,PixelFormats.Pbgra32);
            bitmap.Render(view);var png=new PngBitmapEncoder();png.Frames.Add(BitmapFrame.Create(bitmap));
            using(var output=File.Create(file))png.Save(output);
        }
        internal void Preview(string mode){if(mode=="register")SetMode(true,false);else if(mode=="settings")ShowSettings(true);}
        internal bool Check(){
            return Title=="登陆器"&&C<Image>("Artwork").Source!=null&&C<Button>("Primary").IsVisible&&C<PasswordBox>("Secret")!=null&&C<ComboBox>("Endpoint").IsEditable&&OfSfImport.ImportWindow.CheckLayout();
        }
    }

    internal static class PlayerProgram {
        [STAThread] static int Main(string[] args){
            bool reconnect=Array.IndexOf(args,"--reconnect")>=0,check=Array.IndexOf(args,"--check-ui")>=0;
            int preview=Array.IndexOf(args,"--render-preview");bool visual=preview>=0&&preview+1<args.Length;
            var app=new Application();
            var window=new PlayerWindow(reconnect||Array.IndexOf(args,"--realm-picker")>=0,reconnect,check||visual);
            if(check||visual){
                window.ShowInTaskbar=false;window.ShowActivated=false;window.WindowStartupLocation=WindowStartupLocation.Manual;window.Left=-16000;window.Top=-16000;
                window.Show();window.UpdateLayout();bool ok=window.Check();
                if(visual){window.Preview(preview+2<args.Length?args[preview+2]:"");window.Render(args[preview+1]);}
                window.Close();return ok?0:2;
            }
            return app.Run(window);
        }
    }
}
