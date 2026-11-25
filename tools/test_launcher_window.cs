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
        page=(Window)Activator.CreateInstance(type,P,null,new object[]{false,false,false},null);
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
            Click("Test");Ready();Assert(Status().Contains("连接成功"),"saved CA test: "+Status());
            Click("Options");Click("RegisterTab");Assert(UI<TextBox>("Nickname").IsVisible,"register tab");
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
            Console.WriteLine("PASS actual WPF login/register modes, retained connection, verified TLS, CAPTCHA with wrong answer/refresh, account lookup/proof registration and login, native backup picker");
            return 0;
        }catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}
        finally{if(page.IsVisible)page.Close();app.Shutdown();}
    }
}
