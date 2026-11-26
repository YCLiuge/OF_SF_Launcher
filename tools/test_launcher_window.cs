using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using Forms=System.Windows.Forms;

class LauncherProbe {
    const BindingFlags P=BindingFlags.Instance|BindingFlags.NonPublic;
    static Window page;
    static FrameworkElement view;
    static Type type;
    static T UI<T>(string name) where T:FrameworkElement{return (T)view.FindName(name);}
    static void Click(string name){SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext(Dispatcher.CurrentDispatcher));UI<Button>(name).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));}
    static void Assert(bool value,string label){if(!value)throw new Exception(label);}
    static void Pump(){
        var frame=new DispatcherFrame();
        Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.Background,new Action(()=>frame.Continue=false));
        Dispatcher.PushFrame(frame);
        Forms.Application.DoEvents();Thread.Sleep(15);
    }
    static void Ready(){
        DateTime deadline=DateTime.UtcNow.AddSeconds(90);
        while((bool)type.GetField("busy",P).GetValue(page)){Pump();if(DateTime.UtcNow>deadline)throw new TimeoutException("window pending");}
        Pump();
    }
    static string Status(){return UI<TextBlock>("Status").Text;}
    [STAThread] static int Main(string[] args){
        var app=new Application();app.ShutdownMode=ShutdownMode.OnExplicitShutdown;
        string dir=AppDomain.CurrentDomain.BaseDirectory;
        var assembly=Assembly.LoadFrom(Path.Combine(dir,"OF_SF 登陆器.exe"));
        type=assembly.GetType("OfSfPlayer.PlayerWindow",true);
        bool play=args.Length>0&&args[0]=="play";
        string memoryKey=@"Software\OF_SF\LauncherTests\"+Guid.NewGuid().ToString("N");
        page=(Window)Activator.CreateInstance(type,P,null,play?new object[]{false,false,false}:new object[]{false,false,false,memoryKey},null);
        view=(FrameworkElement)page.Content;
        page.ShowInTaskbar=false;page.ShowActivated=false;page.WindowStartupLocation=WindowStartupLocation.Manual;page.Left=-16000;page.Top=-16000;
        page.Show();Pump();
        try{
            if(play){
                UI<TextBox>("Username").Text=Console.ReadLine();UI<PasswordBox>("Secret").Password=Console.ReadLine();
                Click("Primary");Ready();
                Assert(!page.IsVisible&&File.Exists(Path.Combine(dir,".launcher.pid")),"native launch: "+Status());
                Console.WriteLine("PASS WPF authenticated and launched prepared original Unity executable");return 0;
            }
            Assert(page.Title=="登陆器","title");
            Assert(UI<ComboBox>("Endpoint").Text.Contains("localhost")&&UI<TextBox>("Source").Text.EndsWith("Original"),"legacy migration");
            Click("Options");Assert(UI<StackPanel>("SettingsPanel").IsVisible,"settings");
            string checkFolder=Environment.GetEnvironmentVariable("OF_TEST_CLIENT_INSPECT_DIR");
            string expectedChannel=Environment.GetEnvironmentVariable("OF_TEST_CLIENT_INSPECT_LABEL");
            string originalSource=UI<TextBox>("Source").Text;
            string originalEndpoint=UI<ComboBox>("Endpoint").Text;
            UI<ComboBox>("Endpoint").Text="";
            UI<TextBox>("Source").Text=checkFolder??"";
            Click("InspectSource");Ready();
            Assert(Status().Contains(expectedChannel??"选择游戏"),"offline game version check: "+Status());
            string otherFolder=Environment.GetEnvironmentVariable("OF_TEST_OTHER_CLIENT_DIR");
            if(!String.IsNullOrEmpty(otherFolder)){
                UI<TextBox>("Source").Text=otherFolder;
                Click("InspectSource");Ready();
                Assert(Status().Contains(Environment.GetEnvironmentVariable("OF_TEST_OTHER_CLIENT_LABEL")),"second client channel check: "+Status());
            }
            UI<TextBox>("Source").Text=originalSource;
            UI<ComboBox>("Endpoint").Text=originalEndpoint;
            Click("Test");Ready();Assert(Status().Contains("连接成功"),"saved CA test: "+Status());
            Click("Options");Click("RegisterTab");Assert(UI<TextBox>("Nickname").IsVisible,"register tab");
            Assert(UI<CheckBox>("Remember").IsChecked!=true,"opt-in default");
            UI<CheckBox>("Remember").IsChecked=true;
            UI<TextBox>("Username").Text="本地测试OwO";UI<PasswordBox>("Secret").Password="window-test-password";UI<TextBox>("Nickname").Text="界面测试";
            Click("CheckAccount");Ready();Assert(UI<Image>("CaptchaImage").Source!=null,"actual challenge: "+Status());
            UI<TextBox>("Answer").Text="00000";Click("Primary");Ready();Assert(Status().Contains("新图片"),"reject incorrect answer: "+Status());
            Click("RefreshCaptcha");Ready();UI<TextBox>("Answer").Text="23456";Click("Primary");Ready();
            Assert(Status().Contains("账号可用"),"availability: "+Status());
            Click("Primary");Ready();Assert(Status().Contains("账号已创建"),"create account using proof: "+Status());
            bool importShown=false;var timer=new DispatcherTimer{Interval=TimeSpan.FromMilliseconds(30)};
            timer.Tick+=delegate{
                foreach(Forms.Form w in Forms.Application.OpenForms.Cast<Forms.Form>().ToArray())if(w.GetType().FullName=="OfSfImport.ImportWindow"){
                    importShown=true;timer.Stop();w.Close();break;
                }
            };timer.Start();Click("ImportSave");Ready();timer.Stop();Assert(importShown,"actual save window");
            Click("CheckAccount");Ready();UI<TextBox>("Answer").Text="23456";Click("Primary");Ready();
            Assert(Status().Contains("账号已注册")&&!UI<TextBox>("Nickname").IsVisible,"existing account lookup");
            UI<PasswordBox>("Secret").Password="window-test-password";Click("ImportSave");timer.Start();Ready();timer.Stop();
            Assert(UI<PasswordBox>("Secret").Password.Length==0,"password discarded after login");
            string address=UI<ComboBox>("Endpoint").Text;
            string alternate=Environment.GetEnvironmentVariable("OF_TEST_OTHER_REALM");
            UI<ComboBox>("Endpoint").Text=alternate;Pump();
            Assert(UI<TextBox>("Username").Text==""&&UI<PasswordBox>("Secret").Password==""&&UI<CheckBox>("Remember").IsChecked!=true,"realm isolation");
            Click("RegisterTab");UI<TextBox>("Username").Text="另一区服账号";UI<PasswordBox>("Secret").Password="different-test-password";
            Click("Primary");Ready();UI<TextBox>("Answer").Text="23456";Click("Primary");Ready();
            Assert(Status().Contains("账号已创建"),"second realm registration: "+Status());
            UI<ComboBox>("Endpoint").Text=address;Pump();
            Assert(UI<TextBox>("Username").Text=="本地测试OwO"&&UI<PasswordBox>("Secret").Password=="window-test-password","first realm recover");
            using(var k=Microsoft.Win32.Registry.CurrentUser.OpenSubKey(memoryKey)){
                Assert(k!=null&&k.GetValueNames().Length==2,"one protected entry per realm");
                foreach(string id in k.GetValueNames()){
                    byte[] value=k.GetValue(id) as byte[];
                    Assert(value!=null&&value.Length>32&&!System.Text.Encoding.UTF8.GetString(value).Contains("window-test-password"),"no plaintext registry password");
                }
            }
            Click("Forgot");Assert(Status()=="请联系管理员修改密码","forgot password");
            string newSecret=String.Concat(Enumerable.Repeat(Char.ConvertFromUtf32(0x1f680),128));
            Click("ChangePassword");UI<PasswordBox>("NewPassword").Password=newSecret;UI<PasswordBox>("ConfirmPassword").Password="wrong";
            Click("Primary");Ready();Assert(Status().Contains("不一致"),"confirm mismatch");
            UI<PasswordBox>("ConfirmPassword").Password=newSecret;Click("Primary");Ready();
            Assert(Status().Contains("密码已修改")&&UI<PasswordBox>("Secret").Password==newSecret,"rotated saved secret: "+Status());
            UI<ComboBox>("Endpoint").Text=alternate;Pump();
            Assert(UI<TextBox>("Username").Text=="另一区服账号"&&UI<PasswordBox>("Secret").Password=="","second realm username only");
            UI<ComboBox>("Endpoint").Text=address;Pump();
            Assert(UI<PasswordBox>("Secret").Password==newSecret,"first realm remembered new password");
            UI<CheckBox>("Remember").IsChecked=false;
            Assert(UI<PasswordBox>("Secret").Password=="","cleared on opt-out");
            var reopened=(Window)Activator.CreateInstance(type,P,null,new object[]{false,false,false,memoryKey},null);
            try{
                var node=(FrameworkElement)reopened.Content;
                ((ComboBox)node.FindName("Endpoint")).Text=address;Pump();
                Assert(((TextBox)node.FindName("Username")).Text=="本地测试OwO"&&((PasswordBox)node.FindName("Secret")).Password=="","restart after opt-out");
            }finally{reopened.Close();}
            Console.WriteLine("PASS two TLS realms, DPAPI opt-in, saved username only, password rotation and immediate removal");
            Console.WriteLine("PASS actual WPF login/register modes, retained connection, verified TLS, CAPTCHA with wrong answer/refresh, account lookup/proof registration and login, native backup picker");
            return 0;
        }catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}
        finally{if(page.IsVisible)page.Close();app.Shutdown();if(!play)Microsoft.Win32.Registry.CurrentUser.DeleteSubKeyTree(memoryKey,false);}
    }
}
