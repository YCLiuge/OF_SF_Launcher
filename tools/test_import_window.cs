// Run the shared shipping WinForms controls against an ephemeral TLS test realm.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using OfSfImport;
using Data = System.Collections.Generic.Dictionary<string,object>;

// This type only participates in the smoke-test compilation. The shipped form resolves its
// normal MessageBox.Show to System.Windows.Forms; the test answers the same Yes/No question
// without opening a desktop-wide modal dialog or clicking an unrelated application.
namespace OfSfImport {
    internal static class MessageBox {
        static IWin32Window expectedOwner;
        static DialogResult answer;
        public static void Expect(IWin32Window owner,DialogResult choice){
            if(expectedOwner!=null)throw new Exception("previous confirmation was not consumed");
            expectedOwner=owner;answer=choice;
        }
        public static DialogResult Show(IWin32Window owner,string question,string title,MessageBoxButtons buttons,MessageBoxIcon icon){
            if(expectedOwner!=owner || buttons!=MessageBoxButtons.YesNo || title!="撤销导入" ||
               String.IsNullOrWhiteSpace(question) || icon!=MessageBoxIcon.Question)
                throw new Exception("unexpected import confirmation");
            expectedOwner=null;
            return answer;
        }
        public static void VerifyAnswered(){
            if(expectedOwner!=null)throw new Exception("undo did not ask for confirmation");
        }
    }
}

internal static class ImportWindowSmoke {
    static readonly JavaScriptSerializer json=new JavaScriptSerializer();
    static Data parameters;
    static int undoRequests;
    static T Find<T>(Control parent,Predicate<T> match) where T:Control {
        foreach(Control item in parent.Controls){
            var candidate=item as T;
            if(candidate!=null && match(candidate))return candidate;
            var child=Find(item,match);if(child!=null)return child;
        }
        return null;
    }
    static void Click(Button target){
        if(target==null || !target.Enabled)throw new InvalidOperationException("button unavailable");
        typeof(Button).GetMethod("OnClick",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(target,new object[]{EventArgs.Empty});
    }
    static void UndoChoice(Form owner,Button undo,DialogResult choice){
        OfSfImport.MessageBox.Expect(owner,choice);
        Click(undo);
        OfSfImport.MessageBox.VerifyAnswered();
    }
    static void Wait(Func<bool> done){
        var clock=Stopwatch.StartNew();
        while(!done() && clock.ElapsedMilliseconds<15000){Application.DoEvents();Thread.Sleep(12);}
        if(!done())throw new TimeoutException("import window state did not complete");
    }
    static Task<Data> Request(string action,Data form){
        if(action=="import-undo")undoRequests++;
        var args=new Data(form){{"server",parameters["server"]},{"action",action}};
        return Task.Run(delegate{
            var cmd=new ProcessStartInfo((string)parameters["python"],"-B \""+(string)parameters["api"]+"\""){
                UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true};
            using(var process=Process.Start(cmd)){
                var bytes=Encoding.UTF8.GetBytes(json.Serialize(args));
                process.StandardInput.BaseStream.Write(bytes,0,bytes.Length);process.StandardInput.Close();
                string result=process.StandardOutput.ReadToEnd();process.WaitForExit();
                if(process.ExitCode!=0)throw new IOException("test player account request failed");
                return new JavaScriptSerializer().Deserialize<Data>(result);
            }
        });
    }
    static void CheckIdentityChoices() {
        using(var picker=new IdentityChoices("本服名字")){
            picker.ShowIdentity(new Data{{"uid",1234567},{"nickname","备份名字"},{"uid_status","taken"},{"nickname_status","taken"}});
            var u=Find<ComboBox>(picker,c=>c.AccessibleName=="UID 继承方式");
            var n=Find<ComboBox>(picker,c=>c.AccessibleName=="角色名继承方式");
            var custom=Find<TextBox>(picker,c=>c.AccessibleName=="新的本服角色名");
            if(picker.CanApply)throw new Exception("ambiguous identity accepted");
            u.SelectedIndex=1;n.SelectedIndex=2;
            if(picker.CanApply)throw new Exception("blank custom name accepted");
            custom.Text="新名字";
            var opts=picker.Options();
            if(!picker.CanApply || (string)opts["uid_mode"]!="new" || (string)opts["nickname_mode"]!="custom" ||
                    (string)opts["nickname"]!="新名字")throw new Exception("custom identity choice lost");
            picker.ShowIdentity(new Data{{"uid",1234567},{"nickname","备份名字"},{"uid_status","available"},{"nickname_status","available"}});
            opts=picker.Options();
            if(!picker.CanApply || (string)opts["uid_mode"]!="original" || (string)opts["nickname_mode"]!="original")
                throw new Exception("available original identity not selected");
            picker.ShowIdentity(null);
            if(picker.Options()!=null)throw new Exception("old preview retained identity");
        }
    }
    [STAThread] static int Main(){
        Application.EnableVisualStyles();
        CheckIdentityChoices();
        parameters=json.Deserialize<Data>(Console.In.ReadToEnd());
        foreach(bool apply in new[]{false,true}){
            using(var form=new ImportWindow("localhost","测试角色",(string)parameters["ticket"],Request)){
                var hwnd=form.Handle;
                var source=Find<TextBox>(form,t=>t.AccessibleName=="备份 ZIP、JSON 或结果目录");
                var details=Find<TextBox>(form,t=>t.AccessibleName=="导入预览");
                var preview=Find<Button>(form,b=>b.Text=="预览备份");
                var write=Find<Button>(form,b=>b.Text=="确认导入");
                var undo=Find<Button>(form,b=>b.Text=="撤销上次导入");
                if(source==null || details==null || undo==null || write.Enabled)throw new Exception("invalid initial controls");
                // A shown form has created these controls before Text/AppendText and the first click.
                // Merely creating the hidden parent HWND leaves TextBoxBase lazy and can discard text.
                var sourceHandle=source.Handle;var detailHandle=details.Handle;
                source.Text=(string)parameters["backup"];
                Click(preview);Wait(()=>write.Enabled || preview.Enabled);
                if(!write.Enabled || !details.Text.Contains("确认导入"))throw new Exception("preview not eligible");
                if(apply){
                    Click(write);Wait(()=>preview.Enabled);
                    if(write.Enabled || !details.Text.Contains("导入已写入"))throw new Exception("apply did not complete");
                    string appliedSummary=details.Text;
                    UndoChoice(form,undo,DialogResult.No);
                    if(undoRequests!=0 || details.Text!=appliedSummary || !appliedSummary.Contains("导入已写入"))
                        throw new Exception("cancelled undo changed the applied summary or sent a request");
                    UndoChoice(form,undo,DialogResult.Yes);
                    Wait(()=>preview.Enabled);
                    if(undoRequests!=1 || !details.Text.Contains("已恢复导入前的存档"))throw new Exception("undo did not complete exactly once");
                }else{
                    source.Text+=" ";if(write.Enabled)throw new Exception("changed file kept preview");
                }
                form.Close();
            }
        }
        Console.WriteLine("PASS shared WinForms controls: file selection, preview, cancel, new preview, apply, cancel undo, confirm undo.");
        return 0;
    }
}
